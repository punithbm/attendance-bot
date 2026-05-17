import os
import re
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters, CallbackContext
from database import fetch_user_details, fetch_unpaid_users, update_payment_status, update_followup_date, get_batch_id_for_user, update_pack_payment, mark_user_inactive
from zoom_service import get_attendance_report, format_date_with_ordinal
from datetime import datetime
from urllib.parse import quote
from telegram.ext import JobQueue
from apscheduler.triggers.cron import CronTrigger
import pytz

# Define states for the conversation
INPUT_NAME_OR_PHONE = range(1)

# Load environment variables
load_dotenv()

# Retrieve environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS')
AUTHORIZED_USERNAMES_LIST = AUTHORIZED_USERS.split(',') if AUTHORIZED_USERS else []
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID', '-1004696944070')  # Default to the provided group ID (must include -100 prefix for supergroups)

async def check_user(update: Update) -> bool:
    user_username = update.message.from_user.username
    if user_username not in AUTHORIZED_USERNAMES_LIST:
        await update.message.reply_text("You are not authorized to use this bot.")
        return False
    return True

async def setup_commands(application: Application):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("unpaid", "View unpaid users"),
        BotCommand("paid", "View paid users"),
        BotCommand("userdetails", "Get user details by phone number or name"),
        BotCommand("attendance", "Get today's attendance from Zoom"),
        BotCommand("testschedule", "Test scheduled attendance report (sends to group)")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text(
        'Hello! Here are the available commands:\n'
        '/unpaid - View unpaid users\n'
        '/userdetails - Get user details by phone number or name\n'
        '/attendance - Get attendance report from Zoom'
    )

async def unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    follow_message = quote(
        "Hello \nI noticed you haven't been able to attend our yoga sessions recently. Just wanted to check in to see if everything is okay on your end. Please let me know if there's anything I can assist you with.\nThank You")
    if not await check_user(update):
        return
    users = fetch_unpaid_users(limit=5)
    if not users:
        await update.message.reply_text('No unpaid users found for the current month or earlier.')
        return

    for user in users:
        clean_mobile = ''.join(filter(str.isdigit, user['mobile']))
        start_date = user['start_date'].strftime(
            '%Y-%m-%d') if isinstance(user['start_date'], datetime) else user['start_date']
        last_attended = user['last_date_attended'].strftime(
            '%Y-%m-%d') if isinstance(user['last_date_attended'], datetime) else user['last_date_attended']
        message = f"ID: {user['id']}\n"
        message += f"Name: {user['name']}\n"
        # message += f"Mobile: {clean_mobile}\n"
        message += f"Mobile: <a href='https://wa.me/{clean_mobile}?text={follow_message}' data-telegram-embed='false'>{user['mobile']}</a>\n"
        message += f"Batch id:{user['batch_id']}\n"
        message += f"Due Month: {user['Due_Months']}\n"
        message += f"Due From: {start_date}\n"
        message += f"Last Attended: {last_attended}\n"

        keyboard = [
            [
                InlineKeyboardButton(
                    "Paid", callback_data=f"paid_{user['id']}_{user['Due_Months']}"),
                InlineKeyboardButton(
                    "Followed Up", callback_data=f"followup_{user['id']}_{user['Due_Months']}"),
                InlineKeyboardButton(
                    "Ignore", callback_data=f"ignore_{user['id']}_{user['Due_Months']}"),
            ],
            [
                InlineKeyboardButton(
                    "3 Months", callback_data=f"pack3_{user['id']}_{user['Due_Months']}"),
                InlineKeyboardButton(
                    "6 Months", callback_data=f"pack6_{user['id']}_{user['Due_Months']}"),
                InlineKeyboardButton(
                    "Mark Inactive", callback_data=f"inactive_{user['id']}_{user['Due_Months']}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, reply_markup=reply_markup, disable_web_page_preview=True, parse_mode=ParseMode.HTML)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, user_id, month = query.data.split('_')
    
       # Retrieve the batch_id dynamically
    batch_id = get_batch_id_for_user(user_id)

    if action == 'paid':
        success = update_payment_status(user_id, month, 'paid')
    elif action == 'ignore':
        success = update_payment_status(user_id, month, 'ignore')
    elif action == 'followup':
        success = update_followup_date(user_id, month)
    elif action == 'pack3':
        success = update_pack_payment(user_id, month, 3, 1333, batch_id)
    elif action == 'pack6':
        success = update_pack_payment(user_id, month, 6, 1166, batch_id)
    elif action == 'inactive':
        success = mark_user_inactive(user_id, month)

    if success:
        await query.edit_message_text(f"Action '{action}' completed successfully for user {user_id}.")
    else:
        await query.edit_message_text(f"Failed to complete action '{action}' for user {user_id}.")
        
async def user_details_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the conversation and ask for user details."""
    if not await check_user(update):
        return ConversationHandler.END
    
    await update.message.reply_text(
        "Please provide the phone number or name of the user you want to get details for:"
    )
    return INPUT_NAME_OR_PHONE

async def get_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user input and fetch details."""
    search_term = update.message.text
    user = fetch_user_details(search_term)  # Call the new function

    if not user:
        await update.message.reply_text("No user found with the given phone number or name.")
    else:
        message = (
            f"Name: {user['name']}\n"
            f"Phone number: {user['mobile']}\n"
            f"Batch: {user['batch_id']}\n"
            f"Last payment made date: {user['last_payment_date']}\n"
            f"No. Of days class attended: {user['days_attended']}"
        )
        await update.message.reply_text(message)

    return ConversationHandler.END


async def send_attendance_report(context: ContextTypes.DEFAULT_TYPE, batch_name: str = None):
    """
    Send attendance report to the group.
    This function is called by the scheduler.
    If batch_name is provided, the report is limited to that batch.
    """
    try:
        # Use IST timezone to match scheduler configuration
        ist = pytz.timezone('Asia/Kolkata')
        target_date = datetime.now(ist).strftime('%Y-%m-%d')

        report = await get_attendance_report(target_date, batch_filter=batch_name)

        # Split message if it's too long (Telegram limit is 4096 chars)
        if len(report) > 4000:
            for x in range(0, len(report), 4000):
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=report[x:x+4000],
                    parse_mode=ParseMode.HTML
                )
        else:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=report,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        error_msg = f"An error occurred while sending attendance report: {str(e)}"
        print(error_msg)
        try:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=error_msg
            )
        except:
            pass

def parse_user_date(text: str):
    """
    Parse flexible user date input into 'YYYY-MM-DD'. Returns None if unparseable.
    Accepts: 15-05-2026, 15/05/2026, 2026-05-17, 15 may, 15th May,
             15 may 2026, May 15, etc. When the year is omitted, picks
             the most recent occurrence (this year, or last year if that
             date is still in the future).
    """
    if not text:
        return None
    cleaned = text.strip().lower()
    cleaned = re.sub(r'(\d+)(st|nd|rd|th)\b', r'\1', cleaned)
    cleaned = cleaned.replace(',', ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    formats_with_year = [
        "%d-%m-%Y", "%d/%m/%Y", "%d %m %Y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d %b %Y", "%d %B %Y",
        "%b %d %Y", "%B %d %Y",
    ]
    for fmt in formats_with_year:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    today = datetime.now().date()
    formats_without_year = ["%d %b", "%d %B", "%b %d", "%B %d"]
    for fmt in formats_without_year:
        try:
            dt = datetime.strptime(cleaned, fmt).replace(year=today.year).date()
            if dt > today:
                dt = dt.replace(year=today.year - 1)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


async def attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and display attendance report.
    Usage: /attendance [date]
    Date examples: 15-05-2026, 15/05/2026, 15 may, 15th May, May 15, 2026-05-17.
    """
    if not await check_user(update):
        return

    target_date = None
    if context.args:
        date_input = ' '.join(context.args)
        target_date = parse_user_date(date_input)
        if not target_date:
            await update.message.reply_text(
                "Invalid date format. Try one of:\n"
                "• 15-05-2026\n"
                "• 15 May 2026\n"
                "• 15th May (defaults to most recent)"
            )
            return

    display_date = 'today' if not target_date else format_date_with_ordinal(target_date)
    await update.message.reply_text(f"Fetching attendance data from Zoom for {display_date}... This may take a moment.")
    
    try:
        report = await get_attendance_report(target_date)
        # Split message if it's too long (Telegram limit is 4096 chars)
        if len(report) > 4000:
            for x in range(0, len(report), 4000):
                await update.message.reply_text(report[x:x+4000], parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(report, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {str(e)}")

async def test_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test the scheduled attendance report by sending it immediately to the group.
    Usage: /testschedule        -> sends full-day report
           /testschedule 1..4   -> sends only that batch's report
    """
    if not await check_user(update):
        return

    batch_name = None
    if context.args:
        arg = context.args[0].strip()
        if arg in {"1", "2", "3", "4"}:
            batch_name = f"Batch {arg}"
        else:
            await update.message.reply_text("Invalid argument. Use /testschedule [1|2|3|4].")
            return

    label = batch_name if batch_name else "full day"
    await update.message.reply_text(f"Testing scheduled attendance report ({label})... Sending to group now.")

    # Create a mock context for the send_attendance_report function
    class MockContext:
        def __init__(self, bot):
            self.bot = bot

    mock_context = MockContext(context.bot)
    await send_attendance_report(mock_context, batch_name=batch_name)
    await update.message.reply_text("Test completed! Check the group for the attendance report.")

async def setup_scheduler(application: Application):
    """
    Set up scheduled tasks for sending attendance reports.
    Runs every Monday, Wednesday, and Friday at 9:30 PM IST.
    This is called after the application is initialized.
    """
    job_queue = application.job_queue

    class SchedulerContext:
        def __init__(self, bot):
            self.bot = bot

    scheduler_context = SchedulerContext(application.bot)

    if job_queue is None:
        print("ERROR: JobQueue is not initialized!")
        return

    try:
        job = job_queue.scheduler.add_job(
            send_attendance_report,
            trigger=CronTrigger(
                day_of_week='mon,wed,fri',
                hour=21,
                minute=30,
                timezone=pytz.timezone('Asia/Kolkata')
            ),
            id='weekly_attendance_report',
            name='Send attendance report to group',
            replace_existing=True,
            kwargs={'context': scheduler_context}
        )
        next_run = getattr(job, "next_run_time", None)
        print("Scheduler started: Attendance reports will be sent Mon/Wed/Fri at 9:30 PM IST")
        if next_run:
            print(f"Next run time: {next_run}")
    except Exception as e:
        print(f"ERROR setting up scheduler: {str(e)}")
        import traceback
        traceback.print_exc()

def main():
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(setup_scheduler)
        .build()
    )
    
    # Define the conversation handler
    user_details_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('userdetails', user_details_start)],
        states={
            INPUT_NAME_OR_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_user_details)],
        },
        fallbacks=[]
    )
    
    # Add handlers to the application
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('unpaid', unpaid))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CommandHandler('attendance', attendance))
    application.add_handler(CommandHandler('testschedule', test_schedule))
    application.add_handler(user_details_conv_handler)

    # Start the bot
    print("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
