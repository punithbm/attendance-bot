import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
from database import fetch_unpaid_users, update_payment_status, update_followup_date, update_pack_payment, mark_user_inactive
from datetime import datetime
from urllib.parse import quote

from database import fetch_unpaid_users, fetch_paid_users, update_payment

USER_ID, AMOUNT, MONTHS, BATCH_ID = range(4)

# Load environment variables
load_dotenv()

# Retrieve environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS')
AUTHORIZED_USERNAMES_LIST = AUTHORIZED_USERS.split(
    ',') if AUTHORIZED_USERS else []


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
        BotCommand("update_payment", "Update payment information")
    ]
    await application.bot.set_my_commands(commands)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text(
        'Hello! Here are the available commands:\n'
        '/unpaid - View unpaid users\n'
        '/paid - View paid users\n'
        '/update_payment - Update payment information'
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

    if action == 'paid':
        success = update_payment_status(user_id, month, 'paid')
    elif action == 'ignore':
        success = update_payment_status(user_id, month, 'ignore')
    elif action == 'followup':
        success = update_followup_date(user_id, month)
    elif action == 'pack3':
        success = update_pack_payment(user_id, month, 3, 1333)
    elif action == 'pack6':
        success = update_pack_payment(user_id, month, 6, 1166)
    elif action == 'inactive':
        success = mark_user_inactive(user_id, month)

    if success:
        await query.edit_message_text(f"Action '{action}' completed successfully for user {user_id}.")
    else:
        await query.edit_message_text(f"Failed to complete action '{action}' for user {user_id}.")


async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    users = fetch_paid_users()

    if not users:
        await update.message.reply_text('No paid users found for the current month.')
        return

    message = "Paid Users for this Month:\n\n"
    for user in users:
        clean_mobile = ''.join(filter(str.isdigit, user['mobile']))
        message = f"ID: {user['id']}\n"
        message += f"Name: {user['name']}\n"
        message += f"Mobile: <a href='https://wa.me/{clean_mobile}' data-telegram-embed='false'>{user['mobile']}</a>\n"
        message += f"Amount: {user['amount']}\n\n"

        if len(message) > 4000:
            await update.message.reply_text(message,  disable_web_page_preview=True, parse_mode=ParseMode.HTML)
            message = ""

    if message:
        await update.message.reply_text(message, disable_web_page_preview=True, parse_mode=ParseMode.HTML)


async def update_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text('Please enter the user ID:')
    return USER_ID


async def user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['user_id'] = update.message.text
    await update.message.reply_text('Please enter the amount:')
    return AMOUNT


async def amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['amount'] = float(update.message.text)
        await update.message.reply_text('Please enter the number of months:')
        return MONTHS
    except ValueError:
        await update.message.reply_text('Please enter a valid amount.')
        return AMOUNT


async def months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['months'] = int(update.message.text)
        await update.message.reply_text('Please enter the batch ID:')
        return BATCH_ID
    except ValueError:
        await update.message.reply_text('Please enter a valid number of months.')
        return MONTHS


async def batch_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['batch_id'] = update.message.text
    user_id = context.user_data['user_id']
    amount = context.user_data['amount']
    months = context.user_data['months']
    batch_id = context.user_data['batch_id']

    if update_payment(user_id, amount, months, batch_id):
        await update.message.reply_text('Payment information updated successfully.')
    else:
        await update.message.reply_text('Failed to update payment information.')

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text('Update payment process cancelled.')
    return ConversationHandler.END


def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('update_payment', update_payment_start)],
        states={
            USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_id)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount)],
            MONTHS: [MessageHandler(filters.TEXT & ~filters.COMMAND, months)],
            BATCH_ID: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, batch_id)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('unpaid', unpaid))
    application.add_handler(CommandHandler('paid', paid))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(conv_handler)

    application.run_polling()


if __name__ == '__main__':
    main()
