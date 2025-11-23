import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters, CallbackContext
from database import fetch_user_details, fetch_unpaid_users, update_payment_status, update_followup_date, get_batch_id_for_user, update_pack_payment, mark_user_inactive
from zoom_service import get_attendance_report
from datetime import datetime
from urllib.parse import quote

# Define states for the conversation
INPUT_NAME_OR_PHONE = range(1)

# Load environment variables
load_dotenv()

# Retrieve environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS')
AUTHORIZED_USERNAMES_LIST = AUTHORIZED_USERS.split(',') if AUTHORIZED_USERS else []

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
        BotCommand("attendance", "Get today's attendance from Zoom")
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


async def attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and display attendance report. Usage: /attendance [dd-mm-yyyy]"""
    if not await check_user(update):
        return

    # Check for date argument
    target_date = None
    if context.args:
        date_input = context.args[0]
        try:
            # Parse dd-mm-yyyy
            dt = datetime.strptime(date_input, "%d-%m-%Y")
            # Convert to YYYY-MM-DD for the service
            target_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("Invalid date format. Please use dd-mm-yyyy (e.g., 23-11-2025).")
            return

    await update.message.reply_text(f"Fetching attendance data from Zoom for {'today' if not target_date else target_date}... This may take a moment.")
    
    try:
        report = await get_attendance_report(target_date)
        # Split message if it's too long (Telegram limit is 4096 chars)
        if len(report) > 4000:
            for x in range(0, len(report), 4000):
                await update.message.reply_text(report[x:x+4000], parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {str(e)}")


def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
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
    application.add_handler(user_details_conv_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
