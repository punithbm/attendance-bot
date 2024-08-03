import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters

from database import fetch_unpaid_users, fetch_paid_users, update_payment

USER_ID, AMOUNT, MONTHS, BATCH_ID = range(4)

# Load environment variables
load_dotenv()

# Retrieve environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS')
AUTHORIZED_USERS_LIST = [int(user_id) for user_id in AUTHORIZED_USERS.split(',')] if AUTHORIZED_USERS else []

async def check_user(update: Update) -> bool:
    user_id = update.message.from_user.id
    if user_id not in AUTHORIZED_USERS_LIST:
        await update.message.reply_text("You are not authorized to use this bot.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text('Hello! Use /unpaid to see the list of unpaid users.')
    await update.message.reply_text('Use /paid to see the list of paid users.')
    await update.message.reply_text('Use /update_payment to update payment information.')

async def unpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    users = fetch_unpaid_users()
    
    if not users:
        await update.message.reply_text('No unpaid users found for the current month.')
        return
    
    message = "Unpaid Users for this Month:\n\n"
    for user in users:
        message += f"Name: {user['name']}\n"
        message += f"Mobile: {user['mobile']}\n"
        message += f"Due Months: {user['Due_Months']}\n\n"
        
        if len(message) > 4000:
            await update.message.reply_text(message)
            message = ""
    
    if message:
        await update.message.reply_text(message)

async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    users = fetch_paid_users()
    
    if not users:
        await update.message.reply_text('No paid users found for the current month.')
        return
    
    message = "Paid Users for this Month:\n\n"
    for user in users:
        message += f"Name: {user['name']}\n"
        message += f"Mobile: {user['mobile']}\n"
        message += f"Amount: {user['amount']}\n\n"
        
        if len(message) > 4000:
            await update.message.reply_text(message)
            message = ""
    
    if message:
        await update.message.reply_text(message)

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
            BATCH_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, batch_id)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('unpaid', unpaid))
    application.add_handler(CommandHandler('paid', paid))
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
