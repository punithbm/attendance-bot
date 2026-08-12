"""One-off / re-runnable backfill from Zoom into the database.

For each class in the given history window it:
  1. Resolves every participant's Zoom name to a student via `zoom_aliases`
     (unmapped or ignored names are skipped).
  2. Saves attendance rows (user_id, date, batch_id, status='present') into the
     `attendance` table, de-duplicated by (user_id, date, batch_id).
  3. Tracks each student's earliest class date and writes it as their
     subscription `anchor_date` (first-class anniversary billing anchor).

Run AFTER the Zoom-name mapping has been imported, so aliases exist:
    python backfill_from_zoom.py               # default window
    python backfill_from_zoom.py 2025-01 2026-07   # explicit YYYY-MM range

Note: Zoom's report API only retains a limited history, so classes older than
that won't be found and a few long-standing members may get an anchor equal to
their earliest still-visible class. That's the documented trade-off of deriving
anchors from Zoom.
"""
import asyncio
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from zoom_service import (
    get_zoom_access_token, get_user_id_from_email, get_meetings_by_date_range,
    get_meeting_participants, _match_batch_name, _meeting_ist_date,
    BATCH_IDS, HOST_NAMES, ZOOM_HOST_EMAIL,
)
from database import (
    get_database_connection, fetch_zoom_alias_map, fetch_all_active_users,
    upsert_subscription,
)


def month_range(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _norm(s):
    return ' '.join((s or '').strip().lower().split())


def save_attendance_rows(rows):
    """rows: list of (user_id, date, batch_id). Insert if not already present."""
    if not rows:
        return 0
    conn = get_database_connection()
    cur = conn.cursor()
    inserted = 0
    try:
        for user_id, d, batch_id in rows:
            cur.execute(
                "SELECT 1 FROM attendance WHERE user_id=%s AND date=%s AND batch_id=%s",
                (user_id, d, batch_id),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO attendance (user_id, date, batch_id, status, day)
                   VALUES (%s, %s, %s, 'present', %s)""",
                (user_id, d, batch_id, d.strftime('%A')),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        cur.close()
        conn.close()


async def main(start, end):
    alias_map = fetch_zoom_alias_map()
    if not alias_map:
        print("No zoom_aliases found — import the name mapping first. Aborting.")
        return
    active_ids = {u['id'] for u in fetch_all_active_users()}

    token = await get_zoom_access_token()
    if not token:
        print("Zoom auth failed.")
        return
    host_id = await get_user_id_from_email(token, ZOOM_HOST_EMAIL) or ZOOM_HOST_EMAIL
    batch_lookup = {mid.replace(' ', ''): name for name, mid in BATCH_IDS.items()}

    today = datetime.now().date()
    earliest = {}          # user_id -> earliest date
    attendance_rows = []   # (user_id, date, batch_id)
    seen_keys = set()       # dedupe (user_id, date, batch_id)
    total_classes = 0

    for (year, month) in month_range(start, end):
        first = datetime(year, month, 1).date()
        if first > today:
            break
        nxt = datetime(year + (month == 12), (month % 12) + 1, 1).date()
        last = min(nxt - timedelta(days=1), today)

        meetings = await get_meetings_by_date_range(
            token, host_id, first.strftime('%Y-%m-%d'), last.strftime('%Y-%m-%d'))
        for meeting in meetings:
            batch_name = _match_batch_name(meeting, batch_lookup)
            if not batch_name:
                continue
            mdate = _meeting_ist_date(meeting.get('start_time'))
            if not mdate or mdate.year != year or mdate.month != month:
                continue
            uuid = meeting.get('uuid')
            if not uuid:
                continue
            total_classes += 1
            batch_id = int(batch_name.split()[-1])
            participants = await get_meeting_participants(token, uuid)
            for p in participants:
                name = (p.get('name') or '').strip()
                if not name or name.lower() in HOST_NAMES:
                    continue
                uid = alias_map.get(_norm(name))
                if not uid:              # unmapped (None) or ignored
                    continue
                if uid not in active_ids:
                    continue
                key = (uid, mdate, batch_id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    attendance_rows.append(key)
                if uid not in earliest or mdate < earliest[uid]:
                    earliest[uid] = mdate
        print(f"  {year}-{month:02d}: scanned, running totals -> "
              f"{len(attendance_rows)} attendance rows, {len(earliest)} students")

    inserted = save_attendance_rows(attendance_rows)
    anchors_set = 0
    for uid, d in earliest.items():
        if upsert_subscription(uid, anchor_date=d):
            anchors_set += 1

    print(f"\nDone. Classes scanned: {total_classes}")
    print(f"Attendance rows inserted (new): {inserted}")
    print(f"Anchor dates set: {anchors_set}")


if __name__ == '__main__':
    # Defaults: scan the last ~18 months up to this month.
    start, end = (2025, 1), (datetime.now().year, datetime.now().month)
    if len(sys.argv) == 3:
        sy, sm = sys.argv[1].split('-'); ey, em = sys.argv[2].split('-')
        start, end = (int(sy), int(sm)), (int(ey), int(em))
    print(f"Backfilling Zoom {start} -> {end} ...")
    asyncio.run(main(start, end))
