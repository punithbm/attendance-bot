import os
import re
import io
import csv
import html
import difflib
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters, CallbackContext
from database import (
    fetch_user_details, fetch_unpaid_users, update_payment_status, update_followup_date,
    get_batch_id_for_user, update_pack_payment, mark_user_inactive,
    ensure_payments_table, find_active_users_by_name, find_active_user_by_mobile,
    fetch_all_active_users, record_payment, fetch_paid_students_for_month,
    ensure_zoom_alias_table, set_zoom_alias, fetch_zoom_alias_map,
    ensure_subscriptions_table, upsert_subscription, get_subscription,
    fetch_active_subscriptions, seed_subscriptions_for_active,
)
from zoom_service import get_attendance_report, get_monthly_attendance_summary, format_date_with_ordinal, BATCH_TIMES
import billing
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
        BotCommand("markpaid", "Record a payment (e.g. /markpaid Rashmi 3)"),
        BotCommand("paid", "List payments recorded this month (e.g. /paid jul)"),
        BotCommand("renewals", "Who is due/overdue for renewal (/renewals all for full list)"),
        BotCommand("userdetails", "Get user details by phone number or name"),
        BotCommand("attendance", "Get today's attendance from Zoom"),
        BotCommand("summary", "Monthly attendance summary + CSV (e.g. /summary jun jul)"),
        BotCommand("setalias", "Map a Zoom name to a student (e.g. /setalias iPhone = Rashmi)"),
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

        result = await get_attendance_report(target_date, batch_filter=batch_name)
        attendance_text = result["attendance"]
        recordings = result["recordings"]

        # Split message if it's too long (Telegram limit is 4096 chars)
        if len(attendance_text) > 4000:
            for x in range(0, len(attendance_text), 4000):
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=attendance_text[x:x+4000],
                    parse_mode=ParseMode.HTML
                )
        else:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=attendance_text,
                parse_mode=ParseMode.HTML
            )

        # Send each recording as its own message so it can be forwarded cleanly
        for rec_msg in recordings:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=rec_msg,
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
        result = await get_attendance_report(target_date)
        attendance_text = result["attendance"]
        recordings = result["recordings"]

        # Split message if it's too long (Telegram limit is 4096 chars)
        if len(attendance_text) > 4000:
            for x in range(0, len(attendance_text), 4000):
                await update.message.reply_text(attendance_text[x:x+4000], parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(attendance_text, parse_mode=ParseMode.HTML)

        # Send each recording as its own message so it can be forwarded cleanly
        for rec_msg in recordings:
            await update.message.reply_text(rec_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {str(e)}")

def _normalize_name(name):
    """Lowercase, strip, drop trailing device/junk tokens for matching."""
    return re.sub(r'\s+', ' ', (name or '').strip().lower())


def _names_match(zoom_name, clean_name):
    """Best-effort match between a messy Zoom display name and a clean roster name."""
    a, b = _normalize_name(zoom_name), _normalize_name(clean_name)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # Token overlap: any shared word of length >= 4 (e.g. "Rashmi Sridhar" ~ "Rashmi").
    at = {t for t in a.split() if len(t) >= 4}
    bt = {t for t in b.split() if len(t) >= 4}
    if at & bt:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.86


def _paid_lookup_for_month(year, month_name):
    """Return {'user_ids': set, 'names': [..]} of payments recorded for a month.
    user_ids enable exact paid-matching for students resolved via aliases."""
    try:
        rows = fetch_paid_students_for_month(year, month_name)
    except Exception as e:
        print(f"paid lookup failed: {e}")
        return {"user_ids": set(), "names": []}
    user_ids = set()
    names = []
    for r in rows:
        if r.get("user_id"):
            user_ids.add(r["user_id"])
        n = r.get("user_name") or r.get("student_name")
        if n:
            names.append(n)
    return {"user_ids": user_ids, "names": names}


async def markpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Record a payment for a student (as it arrives, e.g. via WhatsApp).
    Usage: /markpaid <name or phone> [months]
      /markpaid Rashmi Sridhar        -> current month
      /markpaid Rashmi 3              -> 3-month pack from current month
      /markpaid 9876543210 6          -> by phone, 6-month pack
    """
    if not await check_user(update):
        return

    args = list(context.args)
    months = 1
    if args and args[-1].isdigit() and 1 <= int(args[-1]) <= 12:
        months = int(args[-1])
        args = args[:-1]
    term = ' '.join(args).strip()
    if not term:
        await update.message.reply_text(
            "Usage: /markpaid <name or phone> [months]\n"
            "e.g. /markpaid Rashmi Sridhar 3"
        )
        return

    # Find candidate students.
    digits = re.sub(r'\D', '', term)
    if digits and len(digits) >= 7:
        candidates = find_active_user_by_mobile(digits)
    else:
        candidates = find_active_users_by_name(term)
        if not candidates:
            # Fuzzy fallback across all active users.
            everyone = fetch_all_active_users()
            scored = sorted(
                everyone,
                key=lambda u: difflib.SequenceMatcher(
                    None, _normalize_name(term), _normalize_name(u['name'])
                ).ratio(),
                reverse=True,
            )
            candidates = [u for u in scored[:5]
                          if difflib.SequenceMatcher(
                              None, _normalize_name(term), _normalize_name(u['name'])
                          ).ratio() >= 0.5]

    # Stash pending choice so the callback can act without cramming data into 64 bytes.
    context.user_data['markpaid'] = {'term': term, 'months': months, 'candidates': candidates}

    if len(candidates) == 1:
        await _do_mark_paid(update, context, candidates[0], months)
        return

    if candidates:
        buttons = [
            [InlineKeyboardButton(f"{u['name']} (Batch {u['batch_id']})", callback_data=f"mp:{i}")]
            for i, u in enumerate(candidates)
        ]
        buttons.append([InlineKeyboardButton(f"None of these — log as \"{term}\"", callback_data="mp:raw")])
        await update.message.reply_text(
            f"Found {len(candidates)} possible matches for \"{term}\" — pick one "
            f"({months}-month{'s' if months > 1 else ''}):",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        buttons = [[InlineKeyboardButton(f"Log payment as \"{term}\" anyway", callback_data="mp:raw")]]
        await update.message.reply_text(
            f"No student found matching \"{term}\". Check the spelling, or log it as-is:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def _do_mark_paid(update_or_query, context, user, months, raw_name=None):
    """Write the payment and reply. `user` may be None for a raw-name log."""
    paid_through = None
    if user:
        covered = record_payment(
            user_id=user['id'], student_name=user['name'],
            batch_id=user.get('batch_id'), months=months,
        )
        who = f"{user['name']} (Batch {user.get('batch_id')})"
        # Advance the anniversary subscription.
        try:
            sub = get_subscription(user['id']) or {}
            today = datetime.now(pytz.timezone('Asia/Kolkata')).date()
            paid_through = billing.advance_paid_through(
                sub.get('anchor_date'), sub.get('extension_days') or 0,
                sub.get('paid_through'), months, today,
            )
            upsert_subscription(user['id'], paid_through=paid_through)
        except Exception as e:
            print(f"subscription advance failed: {e}")
    else:
        covered = record_payment(
            user_id=None, student_name=raw_name, batch_id=None, months=months,
        )
        who = f"\"{raw_name}\""

    if covered is None:
        text = "Failed to record the payment. Please try again."
    else:
        text = f"✅ Marked PAID: {who}  ({months} month{'s' if months > 1 else ''})"
        if paid_through:
            text += f"\nNow paid through: <b>{paid_through.strftime('%d %b %Y')}</b>"
        elif user is None:
            text += "\n(logged by name — not linked to a student subscription)"

    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:  # callback query
        await update_or_query.edit_message_text(text, parse_mode=ParseMode.HTML)


async def markpaid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the disambiguation buttons from /markpaid."""
    query = update.callback_query
    await query.answer()
    pending = context.user_data.get('markpaid')
    if not pending:
        await query.edit_message_text("This selection expired. Please run /markpaid again.")
        return

    choice = query.data.split(':', 1)[1]
    months = pending['months']
    if choice == 'raw':
        await _do_mark_paid(query, context, None, months, raw_name=pending['term'])
    else:
        try:
            user = pending['candidates'][int(choice)]
        except (ValueError, IndexError):
            await query.edit_message_text("Invalid selection. Please run /markpaid again.")
            return
        await _do_mark_paid(query, context, user, months)
    context.user_data.pop('markpaid', None)


async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List payments recorded for a month.
    Usage: /paid            -> current month
           /paid jun        -> June (most recent)
    """
    if not await check_user(update):
        return

    specs, error = parse_month_specs(context.args)
    if error:
        await update.message.reply_text(error)
        return
    year, month_num = specs[-1]  # use the last given month
    month_name = datetime(year, month_num, 1).strftime('%B')

    rows = fetch_paid_students_for_month(year, month_name)
    if not rows:
        await update.message.reply_text(f"No payments recorded yet for {month_name} {year}.")
        return

    lines = [f"<b>Payments recorded — {month_name} {year} ({len(rows)})</b>\n"]
    for i, r in enumerate(sorted(rows, key=lambda x: (x.get('user_name') or x['student_name']).lower()), 1):
        name = r.get('user_name') or r['student_name']
        batch = f" (Batch {r['batch_id']})" if r.get('batch_id') else ""
        lines.append(f"{i}. {html.escape(name)}{batch}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def renewals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Who is due / overdue for renewal (anniversary billing).
    Usage: /renewals         -> due + overdue students
           /renewals all     -> everyone, including paid-up and unknown
    """
    if not await check_user(update):
        return

    show_all = bool(context.args) and context.args[0].lower() in ('all', 'full')
    today = datetime.now(pytz.timezone('Asia/Kolkata')).date()

    try:
        subs = fetch_active_subscriptions()
    except Exception as e:
        await update.message.reply_text(f"Couldn't load subscriptions: {e}")
        return

    buckets = {'overdue': [], 'due': [], 'paid': [], 'unknown': []}
    for s in subs:
        status, due, days = billing.renewal_status(
            s.get('anchor_date'), s.get('extension_days') or 0,
            s.get('paid_through'), today,
        )
        buckets[status].append({**s, 'status': status, 'due': due, 'days': days})

    def fmt(row):
        name = html.escape(row['name'])
        b = f"B{row['batch_id']}"
        if row['status'] == 'overdue':
            return f"• {name} ({b}) — due {row['due'].strftime('%d %b')}, {row['days']}d overdue"
        if row['status'] == 'due':
            return f"• {name} ({b}) — due {row['due'].strftime('%d %b')} ({row['days']}d)"
        if row['status'] == 'paid':
            return f"• {name} ({b}) — paid till {row['due'].strftime('%d %b')}"
        return f"• {name} ({b}) — no join date yet"

    lines = [f"<b>Renewals as of {today.strftime('%d %b %Y')}</b>"]
    lines.append(f"Overdue: {len(buckets['overdue'])} · Due soon: {len(buckets['due'])} · "
                 f"Paid: {len(buckets['paid'])} · Unknown: {len(buckets['unknown'])}\n")

    for row in sorted(buckets['overdue'], key=lambda r: -r['days']):
        lines.append(fmt(row))
    if buckets['overdue']:
        lines.append("")
    lines.append("<b>Due within a week</b>" if buckets['due'] else "")
    for row in sorted(buckets['due'], key=lambda r: r['due']):
        lines.append(fmt(row))

    if show_all:
        lines.append("\n<b>Paid up</b>")
        for row in sorted(buckets['paid'], key=lambda r: r['due']):
            lines.append(fmt(row))
        if buckets['unknown']:
            lines.append("\n<b>No anchor yet (need first-class date)</b>")
            for row in sorted(buckets['unknown'], key=lambda r: r['name'].lower()):
                lines.append(fmt(row))
    elif buckets['unknown']:
        lines.append(f"\n<i>{len(buckets['unknown'])} students have no join date yet — "
                     f"run the anchor backfill after name mapping. Use /renewals all to list them.</i>")

    text = "\n".join(l for l in lines if l is not None)
    for i in range(0, len(text), 3800):
        await update.message.reply_text(text[i:i+3800], parse_mode=ParseMode.HTML)


async def setalias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Map a Zoom display name to a student (handles duplicates / 2-device joins).
    Usage: /setalias <zoom name> = <student name or phone>
           /setalias <zoom name> = ignore     (drop junk/device names)
    e.g. /setalias iPhone = Rashmi Sridhar
         /setalias Zoom user = ignore
    """
    if not await check_user(update):
        return

    text = ' '.join(context.args)
    if '=' not in text:
        await update.message.reply_text(
            "Usage: /setalias <zoom name> = <student name or phone>\n"
            "e.g. /setalias iPhone = Rashmi Sridhar\n"
            "or:  /setalias Zoom user = ignore"
        )
        return

    zoom_name, target = [part.strip() for part in text.split('=', 1)]
    if not zoom_name or not target:
        await update.message.reply_text("Both a Zoom name and a target are required.")
        return

    if target.lower() in ('ignore', 'skip', 'none', 'junk'):
        ok = set_zoom_alias(zoom_name, None)
        await update.message.reply_text(
            f"✅ \"{zoom_name}\" will now be ignored in summaries." if ok
            else "Failed to save alias."
        )
        return

    # Resolve the target to a student.
    digits = re.sub(r'\D', '', target)
    if digits and len(digits) >= 7:
        candidates = find_active_user_by_mobile(digits)
    else:
        candidates = find_active_users_by_name(target)
        if not candidates:
            everyone = fetch_all_active_users()
            scored = sorted(everyone, key=lambda u: difflib.SequenceMatcher(
                None, _normalize_name(target), _normalize_name(u['name'])).ratio(), reverse=True)
            candidates = [u for u in scored[:5] if difflib.SequenceMatcher(
                None, _normalize_name(target), _normalize_name(u['name'])).ratio() >= 0.5]

    if len(candidates) == 1:
        u = candidates[0]
        ok = set_zoom_alias(zoom_name, u['id'])
        await update.message.reply_text(
            f"✅ Mapped \"{zoom_name}\" → {u['name']} (Batch {u['batch_id']})." if ok
            else "Failed to save alias."
        )
    elif candidates:
        context.user_data['setalias'] = {'zoom_name': zoom_name, 'candidates': candidates}
        buttons = [[InlineKeyboardButton(f"{u['name']} (Batch {u['batch_id']})",
                                         callback_data=f"al:{i}")]
                   for i, u in enumerate(candidates)]
        await update.message.reply_text(
            f"Which student is \"{zoom_name}\"?", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(f"No student found matching \"{target}\".")


async def setalias_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pending = context.user_data.get('setalias')
    if not pending:
        await query.edit_message_text("This selection expired. Please run /setalias again.")
        return
    try:
        u = pending['candidates'][int(query.data.split(':', 1)[1])]
    except (ValueError, IndexError):
        await query.edit_message_text("Invalid selection.")
        return
    ok = set_zoom_alias(pending['zoom_name'], u['id'])
    await query.edit_message_text(
        f"✅ Mapped \"{pending['zoom_name']}\" → {u['name']} (Batch {u['batch_id']})." if ok
        else "Failed to save alias.")
    context.user_data.pop('setalias', None)


_MONTH_NAMES = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10,
    'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}


def parse_month_specs(args):
    """
    Turn command args into a list of (year, month) specs, in order.
    Accepts: month names (jun, june, jul...), or YYYY-MM tokens.
    A bare month uses the most recent occurrence (this year, or last year if
    that month is still in the future). With no args, defaults to the previous
    calendar month followed by the current month.
    Returns (specs, error_message).
    """
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist).date()

    if not args:
        # Previous month, then current month.
        if today.month == 1:
            prev = (today.year - 1, 12)
        else:
            prev = (today.year, today.month - 1)
        return [prev, (today.year, today.month)], None

    specs = []
    seen = set()
    for token in args:
        t = token.strip().lower()
        if not t:
            continue
        year = month = None
        # YYYY-MM or YYYY/MM
        m = re.match(r'^(\d{4})[-/](\d{1,2})$', t)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
        elif t in _MONTH_NAMES:
            month = _MONTH_NAMES[t]
            year = today.year
            if (year, month) > (today.year, today.month):
                year -= 1  # a future month must mean last year
        if not month or not (1 <= month <= 12):
            return None, f"Couldn't understand month '{token}'. Try 'jun', 'july', or '2026-06'."
        key = (year, month)
        if key not in seen:
            seen.add(key)
            specs.append(key)
    if not specs:
        return None, "No valid months given."
    return specs, None


def _has_paid(paid_info):
    """True if any payments were recorded for the month (dict or list form)."""
    if isinstance(paid_info, dict):
        return bool(paid_info.get("user_ids") or paid_info.get("names"))
    return bool(paid_info)


def _student_is_paid(student, paid_info):
    """student is a summary row dict; paid_info is {'user_ids','names'}.
    Exact match by user_id when the student is alias-resolved, else fuzzy by name."""
    if isinstance(paid_info, dict):
        if student.get("user_id") and student["user_id"] in paid_info["user_ids"]:
            return True
        names = paid_info["names"]
    else:  # backward-compat: plain list of names
        names = paid_info
    return any(_names_match(student["name"], pn) for pn in names)


def build_summary_text(result, paid_lists=None):
    """Format the summary dict from get_monthly_attendance_summary as HTML chunks.
    paid_lists: optional list (aligned to months) of recorded paid names, used to
    append a ✅/❌ mark per month."""
    labels = result["month_labels"]
    header = f"<b>Attendance Summary — {' vs '.join(labels)}</b>\n"
    header += "<i>Classes attended per student, from Zoom. ✅ = payment recorded, ❌ = not yet.</i>\n\n"

    if not result["batches"]:
        return [header + "No attendance found for the selected month(s)."]

    chunks = []
    current = header
    for batch_name, students in result["batches"].items():
        time_label = BATCH_TIMES.get(batch_name, "")
        section = f"<b>{html.escape(batch_name)} — {time_label} ({len(students)} students)</b>\n"
        for i, s in enumerate(students, 1):
            parts = []
            for idx, (lbl, c) in enumerate(zip(labels, s["counts"])):
                seg = f"{lbl.split()[0]}: {c}"
                if paid_lists and _has_paid(paid_lists[idx]):
                    seg += " ✅" if _student_is_paid(s, paid_lists[idx]) else " ❌"
                parts.append(seg)
            counts = " | ".join(parts)
            section += f"{i}. {html.escape(s['name'])} — {counts}\n"
        section += "\n"

        # Keep each Telegram message under the 4096-char limit.
        if len(current) + len(section) > 3800:
            chunks.append(current)
            current = ""
        current += section
    if current.strip():
        chunks.append(current)
    return chunks


def build_summary_csv(result, paid_lists=None):
    """Build a CSV (as bytes). The 'Paid?' column is pre-filled 'Yes' where a
    payment is already recorded for the latest month; blank otherwise (fill manually)."""
    labels = result["month_labels"]
    last_idx = len(labels) - 1
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Batch", "Timing", "Student"] + labels + ["Total classes", "Paid?", "Notes"])
    for batch_name, students in result["batches"].items():
        time_label = BATCH_TIMES.get(batch_name, "")
        for s in students:
            paid_flag = ""
            if paid_lists and _has_paid(paid_lists[last_idx]) and _student_is_paid(s, paid_lists[last_idx]):
                paid_flag = "Yes"
            writer.writerow(
                [batch_name, time_label, s["name"]] + s["counts"] + [s["total"], paid_flag, ""]
            )
    return buffer.getvalue().encode("utf-8-sig")  # BOM so Excel/Sheets open cleanly


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monthly attendance summary across all batches, with a CSV export.
    Usage: /summary                -> previous month + current month
           /summary jun jul        -> June and July (most recent)
           /summary 2026-06 2026-07
    """
    if not await check_user(update):
        return

    specs, error = parse_month_specs(context.args)
    if error:
        await update.message.reply_text(error)
        return

    labels = [datetime(y, m, 1).strftime("%b %Y") for (y, m) in specs]
    await update.message.reply_text(
        f"Building attendance summary for {', '.join(labels)} across all batches...\n"
        "This pulls every session from Zoom, so it can take a minute or two."
    )

    # Load alias mappings so messy Zoom names resolve to real students.
    try:
        alias_map = fetch_zoom_alias_map()
        user_names = {u['id']: u['name'] for u in fetch_all_active_users()}
    except Exception as e:
        print(f"alias load failed: {e}")
        alias_map, user_names = {}, {}

    try:
        result = await get_monthly_attendance_summary(
            specs, alias_map=alias_map, user_names=user_names
        )
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {str(e)}")
        return

    if not result.get("ok"):
        await update.message.reply_text(result.get("error") or "Failed to build summary.")
        return

    # Recorded payments per month (for ✅/❌ marks), aligned to `specs`.
    paid_lists = [
        _paid_lookup_for_month(y, datetime(y, m, 1).strftime('%B'))
        for (y, m) in specs
    ]

    # Send the readable summary (chunked to fit Telegram limits).
    for chunk in build_summary_text(result, paid_lists):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

    # Send the CSV so it can be opened, marked up, and used for follow-ups.
    if result["batches"]:
        csv_bytes = build_summary_csv(result, paid_lists)
        filename = "attendance_summary_" + "_".join(l.replace(" ", "") for l in labels) + ".csv"
        await update.message.reply_document(
            document=io.BytesIO(csv_bytes),
            filename=filename,
            caption="Open this in Excel/Google Sheets. 'Paid?' is pre-filled where a payment is recorded; fill the rest and follow up with anyone still blank.",
        )


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
    
    # Make sure the log tables exist, and seed subscriptions for active students
    # (applies the 2026-06-19 two-week extension to everyone active).
    ensure_payments_table()
    ensure_zoom_alias_table()
    ensure_subscriptions_table()
    seeded = seed_subscriptions_for_active(extension_days=14)
    if seeded and seeded > 0:
        print(f"Seeded {seeded} subscriptions (extension_days=14).")

    # Add handlers to the application
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('unpaid', unpaid))
    application.add_handler(CommandHandler('markpaid', markpaid))
    application.add_handler(CommandHandler('paid', paid))
    application.add_handler(CommandHandler('renewals', renewals))
    application.add_handler(CommandHandler('setalias', setalias))
    # Specific callback patterns must be checked before the generic handler.
    application.add_handler(CallbackQueryHandler(markpaid_callback, pattern=r'^mp:'))
    application.add_handler(CallbackQueryHandler(setalias_callback, pattern=r'^al:'))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CommandHandler('attendance', attendance))
    application.add_handler(CommandHandler('summary', summary))
    application.add_handler(CommandHandler('testschedule', test_schedule))
    application.add_handler(user_details_conv_handler)

    # Start the bot
    print("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
