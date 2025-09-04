# ¤¤¤ Start of Final Code ¤¤¤
import logging
import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from requests.auth import HTTPBasicAuth
from typing import Optional, List, Dict, Any

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("zoom_and_ghl_processor.log"), logging.StreamHandler()])

if os.getenv("RENDER") is None:
    from dotenv import load_dotenv
    load_dotenv()

# --- Load Credentials ---
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_WEBINAR_ID = os.getenv("ZOOM_WEBINAR_ID")
GHL_WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL")

if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_WEBINAR_ID, GHL_WEBHOOK_URL]):
    raise RuntimeError("One or more required environment variables are missing.")

app = FastAPI(title="Zoom & GHL Integration API")

# --- Zoom Authentication & Fetching Helpers (Unchanged from last version) ---

def get_zoom_access_token():
    token_url = "https://zoom.us/oauth/token"
    try:
        response = requests.post(token_url, auth=HTTPBasicAuth(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET), params={"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID})
        response.raise_for_status()
        token_data = response.json()
        logging.info("Successfully obtained Zoom access token.")
        return token_data.get("access_token")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail="Could not authenticate with Zoom.")

def _fetch_all_from_zoom(endpoint_url: str, headers: Dict[str, str], data_key: str) -> List[Dict[str, Any]]:
    all_results = []
    params = {"page_size": 300}
    while True:
        try:
            response = requests.get(endpoint_url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            results_on_page = data.get(data_key, [])
            all_results.extend(results_on_page)
            next_page_token = data.get("next_page_token")
            if next_page_token:
                params["next_page_token"] = next_page_token
            else:
                break
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching data from {endpoint_url}: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch {data_key} from Zoom.")
    return all_results

def get_all_past_webinar_participants_from_report(webinar_id: str, access_token: str) -> List[Dict[str, Any]]:
    logging.info(f"Fetching all PARTICIPANTS for past webinar {webinar_id} from reports...")
    url = f"https://api.zoom.us/v2/report/webinars/{webinar_id}/participants"
    headers = {"Authorization": f"Bearer {access_token}"}
    participants = _fetch_all_from_zoom(url, headers, "participants")
    logging.info(f"Found {len(participants)} total participants in the report.")
    return participants

def send_to_ghl_webhook(contact_data: Dict[str, Any]):
    try:
        response = requests.post(GHL_WEBHOOK_URL, json=contact_data)
        response.raise_for_status()
        logging.info(f"Successfully sent {contact_data['email']} to GHL webhook.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send data for {contact_data['email']} to GHL: {e}")

# --- SIMPLIFIED Main Endpoint ---

@app.post("/process-attendees")
async def process_webinar_attendees():
    """
    (Simple Method) Fetches only the webinar participants from the Reporting API
    and sends them to a GHL webhook to be tagged as 'Attended'.
    """
    logging.info("--- Starting Post-Webinar Processing (Participants-Only Method) ---")

    access_token = get_zoom_access_token()
    
    # 1. Fetch the one list we know exists: the participants
    participants = get_all_past_webinar_participants_from_report(ZOOM_WEBINAR_ID, access_token)

    # 2. Process the ATTENDEES (Participants)
    logging.info(f"--- Processing {len(participants)} Attendees ---")
    for person in participants:
        contact_payload = {
            "first_name": person.get("name"), # Reporting API gives full name
            "last_name": "", # GHL can handle an empty last name
            "email": person.get("email"),
            "attended": 1
        }
        send_to_ghl_webhook(contact_payload)
        
    logging.info("--- Attendee Processing Complete ---")
    summary = f"Processing complete. Sent {len(participants)} attendees to GHL."
    logging.info(summary)
    
    return {"message": "Attendee processing complete.", "summary": summary}
# ¤¤¤ End of Final Code ¤¤¤