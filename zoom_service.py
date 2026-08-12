import os
import aiohttp
import base64
import json
import html
from datetime import datetime, timedelta
from dotenv import load_dotenv
from urllib.parse import quote, urlencode

load_dotenv()

def format_date_with_ordinal(date_str):
    """
    Format date string (YYYY-MM-DD) to format like "23rd Nov 2025"
    """
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        day = date_obj.day
        
        # Get ordinal suffix
        if 10 <= day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        # Format: "23rd Nov 2025"
        formatted = date_obj.strftime(f"%d{suffix} %b %Y")
        return formatted
    except:
        return date_str

ZOOM_ACCOUNT_ID = os.getenv('ZOOM_ACCOUNT_ID')
ZOOM_CLIENT_ID = os.getenv('ZOOM_CLIENT_ID')
ZOOM_CLIENT_SECRET = os.getenv('ZOOM_CLIENT_SECRET')
ZOOM_HOST_EMAIL = os.getenv('ZOOM_HOST_EMAIL')

BASE_URL = "https://api.zoom.us/v2"
AUTH_URL = "https://zoom.us/oauth/token"

# Map of Batch Name to Meeting ID (stripped of spaces)
# Batch 1: 06:00 AM - 07:15 AM IST - Yoga with Apoorva
# Batch 2: 07:30 AM - 08:45 AM IST - Yoga with Apoorva
# Batch 3: 11:00 AM - 12:30 PM IST - Yoga with Apoorva
# Batch 4: 06:45 PM - 08:30 PM IST - Yoga with Apoorva
BATCH_IDS = {
    "Batch 1": "86094949374",
    "Batch 2": "89439860664",
    "Batch 3": "86397905588",
    "Batch 4": "81296495600"
}

# Human-readable timing for each batch (used in summary headings).
BATCH_TIMES = {
    "Batch 1": "06:00 AM",
    "Batch 2": "07:30 AM",
    "Batch 3": "11:00 AM",
    "Batch 4": "06:45 PM",
}

# Zoom display names of the host/instructor to exclude from student counts.
HOST_NAMES = {"apoorva yoga", "s p apoorva", "yoga with apoorva", "apoorva"}


async def get_zoom_access_token():
    """
    Obtain an OAuth access token from Zoom using Server-to-Server OAuth.
    """
    auth_header = base64.b64encode(f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "account_credentials",
        "account_id": ZOOM_ACCOUNT_ID
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(AUTH_URL, headers=headers, data=data) as response:
            if response.status == 200:
                result = await response.json()
                return result['access_token']
            else:
                response_text = await response.text()
                print(f"Error getting access token: {response.status} - {response_text}")
                return None

async def get_user_id_from_email(access_token, email):
    """
    Get user ID from email address.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/users/{email}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data.get('id')
            else:
                response_text = await response.text()
                print(f"Error getting user ID for {email}: {response.status} - {response_text}")
                return None

async def get_meetings_by_date_range(access_token, user_id, from_date, to_date):
    """
    Fetch meetings for a user within a date range using the Reports API.
    Returns list of meetings with their UUIDs and meeting IDs.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/report/users/{user_id}/meetings"
    params = {
        "from": from_date,
        "to": to_date,
        "page_size": 300
    }

    all_meetings = []
    next_page_token = None

    async with aiohttp.ClientSession() as session:
        while True:
            if next_page_token:
                params['next_page_token'] = next_page_token

            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    meetings = data.get('meetings', [])
                    all_meetings.extend(meetings)
                    next_page_token = data.get('next_page_token')
                    if not next_page_token:
                        break
                elif response.status == 404:
                    break
                else:
                    response_text = await response.text()
                    print(f"Error fetching meetings: {response.status} - {response_text}")
                    break
    
    return all_meetings

def build_recording_share_url(share_url, play_passcode):
    """
    Embed the play passcode as `?pwd=...` so the recipient can open the
    recording without being prompted for a passcode.
    """
    if not share_url:
        return None
    if not play_passcode or 'pwd=' in share_url:
        return share_url
    separator = '&' if '?' in share_url else '?'
    return f"{share_url}{separator}pwd={play_passcode}"


async def get_meeting_recording(access_token, meeting_uuid):
    """
    Fetch cloud recording info for a meeting instance UUID.
    Returns {'share_url': ..., 'password': ...} if a recording exists, else None.
    Requires the Zoom app scope `cloud_recording:read:admin` — silently returns
    None on 401/403/404 so callers can degrade gracefully.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    original_uuid = meeting_uuid
    if meeting_uuid.startswith('/') or '//' in meeting_uuid:
        meeting_uuid = quote(quote(meeting_uuid, safe=''), safe='')

    url = f"{BASE_URL}/meetings/{meeting_uuid}/recordings"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if not data.get('recording_files'):
                    return None
                return {
                    'share_url': data.get('share_url'),
                    'password': data.get('password'),
                    'play_passcode': data.get('recording_play_passcode'),
                }
            if response.status in (401, 403, 404):
                return None
            response_text = await response.text()
            print(f"Error fetching recording for {original_uuid}: {response.status} - {response_text}")
            return None


async def get_meeting_participants(access_token, meeting_uuid):
    """
    Fetch participants for a specific meeting instance UUID.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    # Double encode UUID if it starts with / or contains special chars, but usually for query param it's fine.
    # For path param, it needs to be double encoded if it contains '/'
    original_uuid = meeting_uuid
    if meeting_uuid.startswith('/') or '//' in meeting_uuid:
         meeting_uuid = quote(quote(meeting_uuid, safe=''), safe='')
         
    url = f"{BASE_URL}/report/meetings/{meeting_uuid}/participants"
    params = {
        "page_size": 300
    }

    participants = []
    next_page_token = None

    async with aiohttp.ClientSession() as session:
        while True:
            if next_page_token:
                params['next_page_token'] = next_page_token

            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    participants.extend(data.get('participants', []))
                    next_page_token = data.get('next_page_token')
                    if not next_page_token:
                        break
                else:
                    response_text = await response.text()
                    print(f"Error fetching participants for {meeting_uuid}: {response.status} - {response_text}")
                    break
    
    return participants

async def get_attendance_report(target_date_str=None, batch_filter=None):
    """
    Orchestrate fetching and formatting the attendance report.
    target_date_str: 'YYYY-MM-DD' format. If None, defaults to today.
    batch_filter: Optional batch name (e.g. "Batch 1") to limit the report to a single batch.
    """
    def _text_only(message):
        return {"attendance": message, "recordings": []}

    token = await get_zoom_access_token()
    if not token:
        return _text_only("Failed to authenticate with Zoom.")

    if not target_date_str:
        target_date_str = datetime.now().strftime('%Y-%m-%d')

    # Parse target date to compare
    try:
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
    except ValueError:
        return _text_only(f"Invalid date format: {target_date_str}. Please use YYYY-MM-DD.")

    # Validate that the date is not in the future
    today = datetime.now().date()
    if target_date > today:
        return _text_only(f"Date {format_date_with_ordinal(target_date_str)} is in the future. Please provide a past date or today's date.")

    # Get user ID from email
    if not ZOOM_HOST_EMAIL:
        return _text_only("ZOOM_HOST_EMAIL not configured in environment variables.")

    user_id = await get_user_id_from_email(token, ZOOM_HOST_EMAIL)
    if not user_id:
        # Try using email directly as user_id (some Zoom accounts allow this)
        user_id = ZOOM_HOST_EMAIL

    # Use the target date for both from and to (same day)
    date_from = target_date_str
    date_to = target_date_str

    # Get all meetings for this date range
    meetings = await get_meetings_by_date_range(token, user_id, date_from, date_to)

    if not meetings:
        return _text_only(f"No meetings found for {format_date_with_ordinal(target_date_str)}.")

    # Create reverse lookup: meeting ID (without spaces) -> batch name
    batch_lookup = {}
    for batch_name, meeting_id in BATCH_IDS.items():
        if batch_filter and batch_name != batch_filter:
            continue
        # Normalize meeting ID (remove spaces for comparison)
        normalized_id = meeting_id.replace(' ', '')
        batch_lookup[normalized_id] = batch_name

    found_batches = {}
    
    # Process each meeting and match to batches
    for meeting in meetings:
        meeting_id_str = str(meeting.get('id', ''))
        # Normalize meeting ID: remove spaces, dashes, and any formatting
        normalized_meeting_id = ''.join(filter(str.isdigit, meeting_id_str))
        
        # Find matching batch by comparing normalized IDs
        batch_name = None
        for batch_id, name in batch_lookup.items():
            # Normalize batch ID the same way
            normalized_batch_id = ''.join(filter(str.isdigit, batch_id))
            if normalized_meeting_id == normalized_batch_id or normalized_meeting_id.endswith(normalized_batch_id):
                batch_name = name
                break
        
        if not batch_name:
            continue
        
        # Parse start time to verify it matches the target date in IST
        start_time_str = meeting.get('start_time')
        if not start_time_str:
            continue
        
        try:
            # Parse UTC time - handle both with and without milliseconds
            if '.' in start_time_str and 'Z' in start_time_str:
                dt_utc = datetime.strptime(start_time_str.split('.')[0] + 'Z', "%Y-%m-%dT%H:%M:%SZ")
            else:
                dt_utc = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ")
            
            # Convert to IST (UTC+5:30)
            dt_ist = dt_utc + timedelta(hours=5, minutes=30)
            
            # Verify the date matches (in IST)
            if dt_ist.date() != target_date:
                continue
        except Exception:
            continue
        
        # Get meeting UUID
        meeting_uuid = meeting.get('uuid')
        if not meeting_uuid:
            continue
        
        # Get participants
        participants = await get_meeting_participants(token, meeting_uuid)

        # Try to fetch cloud recording (None if not recorded or scope missing)
        recording = await get_meeting_recording(token, meeting_uuid)
        
        # Deduplicate by name while keeping max duration (in minutes)
        participant_durations = {}
        for p in participants:
            name = p.get('name')
            if not name:
                continue
            raw_duration = p.get('duration') or p.get('total_duration')
            try:
                duration_seconds = int(raw_duration)
            except (TypeError, ValueError):
                duration_seconds = 0
            
            # Convert seconds to minutes (rounded, min 1 minute if duration > 0)
            if duration_seconds > 0:
                duration = max(1, round(duration_seconds / 60))
            else:
                duration = 0
            
            existing_duration = participant_durations.get(name, 0)
            if duration > existing_duration:
                participant_durations[name] = duration
        
        participant_durations.pop("Apoorva Yoga", None)
        participant_durations.pop("S P Apoorva", None)
        
        if batch_name not in found_batches:
            found_batches[batch_name] = []
        
        found_batches[batch_name].append({
            "topic": batch_name,
            "start_time": start_time_str,
            "recording": recording,
            "participants": [
                {"name": name, "duration": participant_durations[name]}
                for name in sorted(participant_durations.keys())
            ]
        })

    if not found_batches:
        formatted = format_date_with_ordinal(target_date_str)
        if batch_filter:
            return _text_only(f"No meeting found for {batch_filter} on {formatted}.")
        return _text_only(f"No meetings found for {formatted}.")

    # Sort batches for consistent output (filter to requested batch if specified)
    if batch_filter:
        sorted_batches = [batch_filter]
    else:
        sorted_batches = sorted(BATCH_IDS.keys())
    
    # Format date with ordinal (e.g., "23rd Nov 2025") and day name (e.g., "Monday")
    formatted_date = format_date_with_ordinal(target_date_str)
    try:
        day_name = datetime.strptime(target_date_str, '%Y-%m-%d').strftime('%A')
    except Exception:
        day_name = ""
    
    # Use HTML format which is more forgiving with special characters
    final_message = f"<b>Attendance Report for {formatted_date}</b>\n\n"
    recording_messages = []

    for batch in sorted_batches:
        if batch in found_batches:
            total_members = sum(len(m['participants']) for m in found_batches[batch])
            final_message += f"<b>{batch} — {total_members} members</b>\n"
            for meeting in found_batches[batch]:
                # Parse start time for better display
                try:
                    start_time_str = meeting['start_time']
                    if '.' in start_time_str and 'Z' in start_time_str:
                        dt = datetime.strptime(start_time_str.split('.')[0] + 'Z', "%Y-%m-%dT%H:%M:%SZ")
                    else:
                        dt = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ")
                    dt_ist = dt + timedelta(hours=5, minutes=30)
                    time_str = dt_ist.strftime("%I:%M %p")
                except:
                    time_str = meeting['start_time']

                # Escape HTML special characters in time and names
                time_str_escaped = html.escape(time_str) if time_str else ""
                final_message += f"<i>Time: {time_str_escaped}</i>\n"

                recording = meeting.get('recording')
                if recording and recording.get('share_url'):
                    direct_url = build_recording_share_url(
                        recording['share_url'],
                        recording.get('play_passcode'),
                    )
                    escaped_url = html.escape(direct_url, quote=True)
                    date_line = f"{day_name}, {formatted_date}" if day_name else formatted_date
                    recording_messages.append(
                        f"@yogawithapoorva\n\n"
                        f"<b>{html.escape(batch)} — Class Recording</b>\n"
                        f"🗓 {html.escape(date_line)}\n\n"
                        f"{escaped_url}"
                    )

                if meeting['participants']:
                    for i, participant in enumerate(meeting['participants'], 1):
                        escaped_name = html.escape(participant.get('name', "")) if participant.get('name') else ""
                        duration = participant.get('duration', 0)
                        final_message += f"{i}. {escaped_name}  -  {duration} mins\n"
                else:
                    final_message += "No participants found.\n"
            final_message += "\n"

    return {"attendance": final_message, "recordings": recording_messages}


def _meeting_ist_date(start_time_str):
    """Parse a Zoom UTC start_time string into an IST date, or None."""
    if not start_time_str:
        return None
    try:
        if '.' in start_time_str and 'Z' in start_time_str:
            dt_utc = datetime.strptime(start_time_str.split('.')[0] + 'Z', "%Y-%m-%dT%H:%M:%SZ")
        else:
            dt_utc = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ")
        return (dt_utc + timedelta(hours=5, minutes=30)).date()
    except Exception:
        return None


def _match_batch_name(meeting, batch_lookup):
    """Return the batch name for a Zoom meeting, or None if it isn't a class."""
    meeting_id_str = str(meeting.get('id', ''))
    normalized_meeting_id = ''.join(filter(str.isdigit, meeting_id_str))
    for batch_id, name in batch_lookup.items():
        normalized_batch_id = ''.join(filter(str.isdigit, batch_id))
        if normalized_meeting_id == normalized_batch_id or normalized_meeting_id.endswith(normalized_batch_id):
            return name
    return None


async def get_monthly_attendance_summary(month_specs, batch_filter=None, min_minutes=1,
                                         alias_map=None, user_names=None):
    """
    Build a per-student attendance summary across all batches for one or more months.

    month_specs: list of (year, month_int) tuples in display order,
                 e.g. [(2026, 6), (2026, 7)] for June then July.
    batch_filter: optional batch name (e.g. "Batch 1") to limit the report.
    min_minutes: a participant must be present at least this many minutes on a
                 given day to have that day counted (default 1 = present at all).
    alias_map: optional {normalized_zoom_name: user_id_or_None}. A mapped name is
               grouped under that student (so multiple devices/spellings merge and
               same-day joins count once); user_id None means 'ignore this name'.
    user_names: optional {user_id: clean_name} used as the display name for
                resolved students.

    Returns a dict:
      {
        "ok": bool,
        "error": str | None,
        "month_labels": ["Jun 2026", "Jul 2026"],
        "batches": {
            "Batch 1": [
                {"name": "Anita Sharma", "counts": [12, 10], "total": 22},
                ...
            ],
            ...
        }
      }
    counts is aligned to month_specs order.
    """
    month_labels = [datetime(y, m, 1).strftime("%b %Y") for (y, m) in month_specs]

    def _fail(msg):
        return {"ok": False, "error": msg, "month_labels": month_labels, "batches": {}}

    token = await get_zoom_access_token()
    if not token:
        return _fail("Failed to authenticate with Zoom.")

    if not ZOOM_HOST_EMAIL:
        return _fail("ZOOM_HOST_EMAIL not configured in environment variables.")

    user_id = await get_user_id_from_email(token, ZOOM_HOST_EMAIL) or ZOOM_HOST_EMAIL

    # Which batches to include.
    batch_lookup = {}
    for batch_name, meeting_id in BATCH_IDS.items():
        if batch_filter and batch_name != batch_filter:
            continue
        batch_lookup[meeting_id.replace(' ', '')] = batch_name

    today = datetime.now().date()

    # attendance[batch_name][normalized_name] = {
    #     "display": original name, "days": [set_of_dates_per_month] }
    attendance = {}

    for month_index, (year, month) in enumerate(month_specs):
        first_day = datetime(year, month, 1).date()
        # Last day of month, capped at today (no future dates).
        if month == 12:
            next_month_first = datetime(year + 1, 1, 1).date()
        else:
            next_month_first = datetime(year, month + 1, 1).date()
        last_day = next_month_first - timedelta(days=1)
        if last_day > today:
            last_day = today
        if first_day > today:
            continue  # entire month is in the future

        meetings = await get_meetings_by_date_range(
            token, user_id,
            first_day.strftime('%Y-%m-%d'),
            last_day.strftime('%Y-%m-%d'),
        )

        for meeting in meetings:
            batch_name = _match_batch_name(meeting, batch_lookup)
            if not batch_name:
                continue
            meeting_date = _meeting_ist_date(meeting.get('start_time'))
            if not meeting_date or meeting_date.year != year or meeting_date.month != month:
                continue
            meeting_uuid = meeting.get('uuid')
            if not meeting_uuid:
                continue

            participants = await get_meeting_participants(token, meeting_uuid)

            # Best (max) duration per name for this session.
            durations = {}
            for p in participants:
                name = (p.get('name') or '').strip()
                if not name or name.lower() in HOST_NAMES:
                    continue
                raw = p.get('duration') or p.get('total_duration')
                try:
                    seconds = int(raw)
                except (TypeError, ValueError):
                    seconds = 0
                minutes = max(1, round(seconds / 60)) if seconds > 0 else 0
                if minutes > durations.get(name, 0):
                    durations[name] = minutes

            alias_map = alias_map or {}
            user_names = user_names or {}
            for name, minutes in durations.items():
                if minutes < min_minutes:
                    continue
                norm = ' '.join(name.strip().lower().split())
                if norm in alias_map:
                    uid = alias_map[norm]
                    if uid is None:
                        continue  # explicitly ignored (host/junk/device)
                    identity_key = ('u', uid)
                    display = user_names.get(uid, name)
                    resolved_uid = uid
                else:
                    identity_key = ('z', norm)
                    display = name
                    resolved_uid = None
                batch_bucket = attendance.setdefault(batch_name, {})
                rec = batch_bucket.setdefault(
                    identity_key,
                    {"display": display, "user_id": resolved_uid,
                     "days": [set() for _ in month_specs]},
                )
                # Same student on two devices the same day => one class.
                rec["days"][month_index].add(meeting_date)

    # Shape the result.
    batches_out = {}
    ordered_batches = [batch_filter] if batch_filter else sorted(BATCH_IDS.keys())
    for batch_name in ordered_batches:
        bucket = attendance.get(batch_name)
        if not bucket:
            continue
        students = []
        for rec in bucket.values():
            counts = [len(day_set) for day_set in rec["days"]]
            students.append({
                "name": rec["display"],
                "user_id": rec.get("user_id"),
                "counts": counts,
                "total": sum(counts),
            })
        students.sort(key=lambda s: s["name"].lower())
        batches_out[batch_name] = students

    return {"ok": True, "error": None, "month_labels": month_labels, "batches": batches_out}
