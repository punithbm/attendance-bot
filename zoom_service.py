import os
import aiohttp
import base64
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from urllib.parse import quote

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

async def get_past_meeting_instances(access_token, meeting_id):
    """
    Fetch past instances of a recurring meeting.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{BASE_URL}/past_meetings/{meeting_id}/instances"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data.get('meetings', [])
            elif response.status == 404:
                # Meeting ID not found or expired
                return []
            else:
                error_text = await response.text()
                print(f"Error fetching instances for {meeting_id}: {response.status} - {error_text}")
                return []

async def get_meeting_participants(access_token, meeting_uuid):
    """
    Fetch participants for a specific meeting instance UUID.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    # Double encode UUID if it starts with / or contains special chars, but usually for query param it's fine.
    # For path param, it needs to be double encoded if it contains '/'
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
                    error_text = await response.text()
                    print(f"Error fetching participants for {meeting_uuid}: {response.status} - {error_text}")
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

    found_batches = {}
    
    # Sort batches to process in order
    sorted_batches = sorted(BATCH_IDS.keys())

    for batch_name in sorted_batches:
        meeting_id = BATCH_IDS[batch_name]
        instances = await get_past_meeting_instances(token, meeting_id)
        
        # Find instance that matches the target date
        matched_instance = None
        for instance in instances:
            start_time_str = instance.get('start_time') # UTC time, e.g., 2025-11-23T10:50:26Z
            if not start_time_str:
                continue
                
            try:
                # Parse UTC time
                dt_utc = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ")
                # Convert to IST (UTC+5:30)
                dt_ist = dt_utc + timedelta(hours=5, minutes=30)
                
                if dt_ist.date() == target_date:
                    matched_instance = instance
                    # We found the instance for this date, break (assuming one per day per batch)
                    # If there are multiple, we might want the latest? But usually one class per day.
                    # Let's take the last one found if multiple? Or list all?
                    # For now, let's assume one and take it.
                    # Actually, if there are multiple, we should probably list them all?
                    # But the structure is designed for one. Let's stick to the first one found or last?
                    # Instances are usually returned most recent first? No, documentation says "start_time" order?
                    # Let's just pick the first one matching the date.
                    break
            except Exception as e:
                print(f"Error parsing date {start_time_str}: {e}")
                continue
        
        if matched_instance:
            if batch_name not in found_batches:
                found_batches[batch_name] = []
            
            uuid = matched_instance.get('uuid')
            start_time = matched_instance.get('start_time')
            
            participants = await get_meeting_participants(token, uuid)
            
            # Deduplicate by name
            unique_names = set()
            for p in participants:
                name = p.get('name')
                if name:
                    unique_names.add(name)
            
            unique_names.discard("Apoorva Yoga") 
            
            found_batches[batch_name].append({
                "topic": batch_name, # Use batch name as topic since we know it
                "start_time": start_time,
                "participants": sorted(list(unique_names))
            })

    if not found_batches:
        return f"No Batch meetings found for {target_date_str}."

    final_message = f"**Attendance Report for {target_date_str}**\n\n"
    
    for batch in sorted_batches:
        if batch in found_batches:
            final_message += f"**{batch}**\n"
            for meeting in found_batches[batch]:
                # Parse start time for better display
                try:
                    dt = datetime.strptime(meeting['start_time'], "%Y-%m-%dT%H:%M:%SZ")
                    dt_ist = dt + timedelta(hours=5, minutes=30)
                    time_str = dt_ist.strftime("%I:%M %p")
                except:
                    time_str = meeting['start_time']

                final_message += f"_Time: {time_str}_\n"
                if meeting['participants']:
                    for name in meeting['participants']:
                        final_message += f"- {name}\n"
                else:
                    final_message += "No participants found.\n"
            final_message += "\n"

    return final_message
