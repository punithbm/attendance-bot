import os
import aiohttp
import base64
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

ZOOM_ACCOUNT_ID = os.getenv('ZOOM_ACCOUNT_ID')
ZOOM_CLIENT_ID = os.getenv('ZOOM_CLIENT_ID')
ZOOM_CLIENT_SECRET = os.getenv('ZOOM_CLIENT_SECRET')
ZOOM_HOST_EMAIL = os.getenv('ZOOM_HOST_EMAIL')

BASE_URL = "https://api.zoom.us/v2"
AUTH_URL = "https://zoom.us/oauth/token"

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
                error_text = await response.text()
                print(f"Error getting access token: {response.status} - {error_text}")
                return None

async def get_past_meetings(access_token, date_str):
    """
    Fetch past meetings for the host on a specific date.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    # Zoom API format for date is YYYY-MM-DD
    # We want meetings that started on this date.
    # List meetings endpoint: /users/{userId}/meetings (scheduled) or /metrics/meetings (dashboard)
    # Better to use /report/users/{userId}/meetings for past meetings
    
    # Using /report/users/{userId}/meetings
    url = f"{BASE_URL}/report/users/{ZOOM_HOST_EMAIL}/meetings"
    params = {
        "from": date_str,
        "to": date_str,
        "page_size": 300,
        "type": "past"
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as response:
            if response.status == 200:
                data = await response.json()
                # Debugging: Print the raw data to console/logs
                print(f"DEBUG: Fetched meetings for {date_str}: {data}")
                return data.get('meetings', [])
            else:
                error_text = await response.text()
                print(f"Error fetching meetings: {response.status} - {error_text}")
                return []

async def get_meeting_participants(access_token, meeting_id):
    """
    Fetch participants for a specific meeting.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/report/meetings/{meeting_id}/participants"
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
                    error_text = await response.text()
                    print(f"Error fetching participants: {response.status} - {error_text}")
                    break
    
    return participants

async def get_attendance_report():
    """
    Orchestrate fetching and formatting the attendance report.
    """
    token = await get_zoom_access_token()
    if not token:
        return "Failed to authenticate with Zoom."

    today = datetime.now().strftime('%Y-%m-%d')
    # Try fetching for yesterday and today to handle timezone differences
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Fetch both and combine
    meetings_today = await get_past_meetings(token, today)
    meetings_yesterday = await get_past_meetings(token, yesterday)
    
    meetings = meetings_today + meetings_yesterday
    
    
    if not meetings:
        return f"No meetings found for today ({today}) or yesterday ({yesterday}). Debug info: Checked email {ZOOM_HOST_EMAIL}"

    # Map of Batch Name to Meeting ID (stripped of spaces)
    BATCH_IDS = {
        "83527645001": "Batch 1",
        "88002278840": "Batch 2",
        "81387781923": "Batch 3",
        "88554007453": "Batch 4"
    }
    
    found_batches = {}

    for meeting in meetings:
        topic = meeting.get('topic', '')
        meeting_id = str(meeting.get('id')) # Ensure it's a string
        start_time = meeting.get('start_time')
        
        # Check if meeting_id matches one of our batches
        matched_batch = BATCH_IDS.get(meeting_id)
        
        # Fallback to topic matching if ID doesn't match (just in case)
        if not matched_batch:
             for batch_name in ["Batch 1", "Batch 2", "Batch 3", "Batch 4"]:
                if batch_name.lower() in topic.lower():
                    matched_batch = batch_name
                    break
        
        if matched_batch:
            if matched_batch not in found_batches:
                found_batches[matched_batch] = []
            
            # Use the UUID for fetching participants if available, otherwise ID
            # The API for participants usually takes the UUID for past meetings to be precise, 
            # or the meetingId (which might return the latest if not careful, but here we are iterating past meetings)
            # Actually, for /report/meetings/{meetingId}/participants, meetingId can be the number or UUID.
            # Using UUID is safer for past meetings.
            meeting_uuid = meeting.get('uuid')
            
            # If uuid starts with /, it needs to be double encoded, but usually it's fine.
            # Let's try using the numeric ID first as it's simpler, or UUID if ID fails? 
            # The previous code used meeting_id (numeric). Let's stick to that but maybe try UUID if needed.
            # Actually, for past meetings, using the UUID is recommended.
            
            participants = await get_meeting_participants(token, meeting_uuid if meeting_uuid else meeting_id)
            
            # Deduplicate by name
            unique_names = set()
            for p in participants:
                name = p.get('name')
                if name:
                    unique_names.add(name)
            
            unique_names.discard("Apoorva Yoga") 
            
            found_batches[matched_batch].append({
                "topic": topic,
                "start_time": start_time,
                "participants": sorted(list(unique_names))
            })

    if not found_batches:
        return f"No Batch meetings found for today ({today})."

    final_message = f"**Attendance Report for {today}**\n\n"
    
    # Sort batches to ensure 1, 2, 3, 4 order
    sorted_batch_keys = sorted(found_batches.keys())
    
    for batch in sorted_batch_keys:
        final_message += f"**{batch}**\n"
        for meeting in found_batches[batch]:
            # Parse start time for better display
            try:
                dt = datetime.strptime(meeting['start_time'], "%Y-%m-%dT%H:%M:%SZ")
                # Convert to IST roughly or just show UTC? User is in IST (screenshot shows IST).
                # Adding 5:30 for IST
                dt_ist = dt + timedelta(hours=5, minutes=30)
                time_str = dt_ist.strftime("%I:%M %p")
            except:
                time_str = meeting['start_time']

            final_message += f"_{meeting['topic']} ({time_str})_\n"
            if meeting['participants']:
                for name in meeting['participants']:
                    final_message += f"- {name}\n"
            else:
                final_message += "No participants found.\n"
        final_message += "\n"

    return final_message
