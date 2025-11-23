import os
import aiohttp
import base64
import json
import html
from datetime import datetime, timedelta
from dotenv import load_dotenv
from urllib.parse import quote, urlencode

load_dotenv()

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

def escape_markdown_v2(text):
    """
    Escape special characters for Telegram MarkdownV2 format.
    """
    if not text:
        return text
    # Characters that need escaping in MarkdownV2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    escaped = str(text)
    for char in special_chars:
        escaped = escaped.replace(char, f'\\{char}')
    return escaped

def escape_markdown(text):
    """
    Escape special characters for Telegram Markdown format (simpler version).
    """
    if not text:
        return text
    # Characters that need escaping in Markdown
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    escaped = str(text)
    for char in special_chars:
        escaped = escaped.replace(char, f'\\{char}')
    return escaped

def log_api_request(method, url, headers=None, params=None, data=None, body=None):
    """
    Log API request details in a format suitable for Postman testing.
    """
    print("\n" + "="*80)
    print("ZOOM API REQUEST - POSTMAN FORMAT")
    print("="*80)
    print(f"Method: {method}")
    print(f"URL: {url}")
    
    if params:
        # Build URL with query params
        query_string = urlencode(params)
        full_url = f"{url}?{query_string}" if query_string else url
        print(f"Full URL with params: {full_url}")
        print(f"\nQuery Parameters:")
        for key, value in params.items():
            print(f"  {key}: {value}")
    
    if headers:
        print(f"\nHeaders:")
        for key, value in headers.items():
            # Mask access token for security but show format
            if key.lower() == 'authorization' and 'Bearer' in value:
                masked_token = value[:20] + "..." + value[-10:] if len(value) > 30 else "***MASKED***"
                print(f"  {key}: {masked_token}")
            elif key.lower() == 'authorization' and 'Basic' in value:
                print(f"  {key}: Basic ***MASKED***")
            else:
                print(f"  {key}: {value}")
    
    if data:
        print(f"\nBody (form-data):")
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"  {key}: {value}")
        else:
            print(f"  {data}")
    
    if body:
        print(f"\nBody (JSON):")
        if isinstance(body, dict):
            print(json.dumps(body, indent=2))
        else:
            print(body)
    
    print("="*80 + "\n")

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

    # Log request for Postman
    log_api_request("POST", AUTH_URL, headers=headers, data=data)

    async with aiohttp.ClientSession() as session:
        async with session.post(AUTH_URL, headers=headers, data=data) as response:
            response_text = await response.text()
            print(f"Response Status: {response.status}")
            if response.status == 200:
                result = await response.json()
                print(f"Response: Success - Token obtained")
                return result['access_token']
            else:
                print(f"Response Error: {response_text}")
                return None

async def get_user_id_from_email(access_token, email):
    """
    Get user ID from email address.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/users/{email}"

    # Log request for Postman
    log_api_request("GET", url, headers=headers)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            response_text = await response.text()
            print(f"Response Status: {response.status}")
            if response.status == 200:
                data = await response.json()
                print(f"Response: {json.dumps(data, indent=2)}")
                return data.get('id')
            else:
                print(f"Response Error: {response_text}")
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

    # Log request for Postman
    log_api_request("GET", url, headers=headers, params=params)

    all_meetings = []
    next_page_token = None
    page_num = 1

    async with aiohttp.ClientSession() as session:
        while True:
            if next_page_token:
                params['next_page_token'] = next_page_token
                print(f"\nFetching page {page_num} (with next_page_token)...")

            async with session.get(url, headers=headers, params=params) as response:
                response_text = await response.text()
                print(f"Response Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    meetings = data.get('meetings', [])
                    print(f"Found {len(meetings)} meetings on page {page_num}")
                    all_meetings.extend(meetings)
                    next_page_token = data.get('next_page_token')
                    if not next_page_token:
                        print(f"Total meetings found: {len(all_meetings)}")
                        break
                    page_num += 1
                elif response.status == 404:
                    print(f"Response: No meetings found for user {user_id} in date range {from_date} to {to_date}")
                    break
                else:
                    print(f"Response Error: {response_text}")
                    # Check for scope/permission errors
                    try:
                        error_data = json.loads(response_text)
                        if error_data.get('code') == 4711 or 'scope' in error_data.get('message', '').lower():
                            print("\n" + "!"*80)
                            print("PERMISSION ERROR: Missing required Zoom API scope!")
                            print("!"*80)
                            print("Required scope: report:read:user:admin")
                            print("\nTo fix this:")
                            print("1. Go to https://marketplace.zoom.us/")
                            print("2. Navigate to 'Develop' > 'Build App' > Your App")
                            print("3. Go to the 'Scopes' tab")
                            print("4. Add the scope: 'View user meeting reports' (report:read:user:admin)")
                            print("5. Save and re-authorize your app")
                            print("6. Restart the bot service")
                            print("!"*80 + "\n")
                    except:
                        pass
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

    # Log request for Postman
    log_api_request("GET", url, headers=headers, params=params)

    participants = []
    next_page_token = None
    page_num = 1

    async with aiohttp.ClientSession() as session:
        while True:
            if next_page_token:
                params['next_page_token'] = next_page_token
                print(f"\nFetching participants page {page_num} (with next_page_token)...")

            async with session.get(url, headers=headers, params=params) as response:
                response_text = await response.text()
                print(f"Response Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    page_participants = data.get('participants', [])
                    print(f"Found {len(page_participants)} participants on page {page_num}")
                    participants.extend(page_participants)
                    next_page_token = data.get('next_page_token')
                    if not next_page_token:
                        print(f"Total participants found: {len(participants)}")
                        break
                    page_num += 1
                else:
                    print(f"Response Error: {response_text}")
                    # Check for scope/permission errors
                    try:
                        error_data = json.loads(response_text)
                        if error_data.get('code') == 4711 or 'scope' in error_data.get('message', '').lower():
                            print("\n" + "!"*80)
                            print("PERMISSION ERROR: Missing required Zoom API scope!")
                            print("!"*80)
                            print("Required scope: report:read:user:admin")
                            print("\nTo fix this:")
                            print("1. Go to https://marketplace.zoom.us/")
                            print("2. Navigate to 'Develop' > 'Build App' > Your App")
                            print("3. Go to the 'Scopes' tab")
                            print("4. Add the scope: 'View user meeting reports' (report:read:user:admin)")
                            print("5. Save and re-authorize your app")
                            print("6. Restart the bot service")
                            print("!"*80 + "\n")
                    except:
                        pass
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
        print(f"Could not get user ID, trying email directly: {user_id}")

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
            # Debug: print unmatched meeting IDs
            print(f"Unmatched meeting ID: {meeting_id_str} (normalized: {normalized_meeting_id})")
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
        except Exception as e:
            print(f"Error parsing date {start_time_str}: {e}")
            continue
        
        # Get meeting UUID
        meeting_uuid = meeting.get('uuid')
        if not meeting_uuid:
            print(f"Warning: No UUID found for meeting {meeting_id_str}")
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
    
    # Use HTML format which is more forgiving with special characters
    final_message = f"<b>Attendance Report for {target_date_str}</b>\n\n"
    
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
                    for name in meeting['participants']:
                        # Escape HTML special characters in participant names
                        escaped_name = html.escape(name) if name else ""
                        final_message += f"• {escaped_name}\n"
                else:
                    final_message += "No participants found.\n"
            final_message += "\n"

    return final_message
