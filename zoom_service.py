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
BATCH_IDS = {
    "Batch 1": "83527645001",
    "Batch 2": "88002278840",
    "Batch 3": "81387781923",
    "Batch 4": "88554007453"
}


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

async def get_attendance_report(target_date_str=None):
    """
    Orchestrate fetching and formatting the attendance report.
    target_date_str: 'YYYY-MM-DD' format. If None, defaults to today.
    """
    token = await get_zoom_access_token()
    if not token:
        return "Failed to authenticate with Zoom."

    if not target_date_str:
        target_date_str = datetime.now().strftime('%Y-%m-%d')
    
    # Parse target date to compare
    try:
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
    except ValueError:
        return f"Invalid date format: {target_date_str}. Please use YYYY-MM-DD."

    # Validate that the date is not in the future
    today = datetime.now().date()
    if target_date > today:
        return f"Date {target_date_str} is in the future. Please provide a past date or today's date."

    # Get user ID from email
    if not ZOOM_HOST_EMAIL:
        return "ZOOM_HOST_EMAIL not configured in environment variables."
    
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
        return f"No meetings found for {target_date_str}.\n\nPossible reasons:\n- No meetings occurred on this date\n- API permissions may be insufficient\n- User ID/email may be incorrect"

    # Create reverse lookup: meeting ID (without spaces) -> batch name
    batch_lookup = {}
    for batch_name, meeting_id in BATCH_IDS.items():
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
        
        # Deduplicate by name
        unique_names = set()
        for p in participants:
            name = p.get('name')
            if name:
                unique_names.add(name)
        
        unique_names.discard("Apoorva Yoga")
        unique_names.discard("S P Apoorva")
        
        if batch_name not in found_batches:
            found_batches[batch_name] = []
        
        found_batches[batch_name].append({
            "topic": batch_name,
            "start_time": start_time_str,
            "participants": sorted(list(unique_names))
        })

    if not found_batches:
        return f"No Batch meetings found for {target_date_str}.\n\nFound {len(meetings)} meeting(s) on this date, but none matched the configured batch meeting IDs."

    # Sort batches for consistent output
    sorted_batches = sorted(BATCH_IDS.keys())
    
    # Format date with ordinal (e.g., "23rd Nov 2025")
    formatted_date = format_date_with_ordinal(target_date_str)
    
    # Use HTML format which is more forgiving with special characters
    final_message = f"<b>Attendance Report for {formatted_date}</b>\n\n"
    
    for batch in sorted_batches:
        if batch in found_batches:
            final_message += f"<b>{batch}</b>\n"
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
                
                if meeting['participants']:
                    for i, name in enumerate(meeting['participants'], 1):
                        # Escape HTML special characters in participant names
                        escaped_name = html.escape(name) if name else ""
                        final_message += f"{i}. {escaped_name}\n"
                else:
                    final_message += "No participants found.\n"
            final_message += "\n"

    return final_message
