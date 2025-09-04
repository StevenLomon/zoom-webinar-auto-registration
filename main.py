# ¤¤¤ Start of Code ¤¤¤
import logging
import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from requests.auth import HTTPBasicAuth
from typing import Optional, List, Dict, Any

# --- Basic Configuration ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("zoom_and_ghl_processor.log"),
        logging.StreamHandler()
    ]
)

if os.getenv("RENDER") is None:
    from dotenv import load_dotenv
    load_dotenv()

# --- Load Credentials from Environment ---

ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_WEBINAR_ID = os.getenv("ZOOM_WEBINAR_ID")
GHL_WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL")

if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_WEBINAR_ID, GHL_WEBHOOK_URL]):
    error_message = "One or more required environment variables are missing (Zoom or GHL)."
    logging.error(error_message)
    raise RuntimeError(error_message)

# --- FastAPI App Initialization ---

app = FastAPI(
    title="Zoom & GHL Integration API",
    description="An API to register users for a Zoom webinar and process attendance post-webinar."
)

# --- Pydantic Data Models (Unchanged) ---

class Registrant(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    email: EmailStr

# --- Zoom Authentication Helper (Unchanged) ---

def get_zoom_access_token():
    # ... (This function is unchanged)
    token_url = "https://zoom.us/oauth/token"
    try:
        response = requests.post(
            token_url,
            auth=HTTPBasicAuth(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET),
            params={"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID}
        )
        response.raise_for_status()
        token_data = response.json()
        logging.info("Successfully obtained Zoom access token.")
        return token_data.get("access_token")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to get Zoom access token: {e}")
        raise HTTPException(status_code=500, detail="Could not authenticate with Zoom.")

# --- Zoom Data Fetching Helpers ---

def _fetch_all_from_zoom(endpoint_url: str, headers: Dict[str, str], data_key: str) -> List[Dict[str, Any]]:
    # ... (This generic helper is unchanged)
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

# --- MODIFIED: Using Reporting API for both functions ---

def get_all_past_webinar_registrants_from_report(webinar_id: str, access_token: str) -> List[Dict[str, Any]]:
    """Fetches all registrants for a given PAST webinar from the reporting API."""
    logging.info(f"Fetching all REGISTRANTS for past webinar {webinar_id} from reports...")
    url = f"https://api.zoom.us/v2/report/webinars/{webinar_id}/registrants"
    headers = {"Authorization": f"Bearer {access_token}"}
    registrants = _fetch_all_from_zoom(url, headers, "registrants")
    logging.info(f"Found {len(registrants)} total registrants in the report.")
    return registrants

def get_all_past_webinar_participants_from_report(webinar_id: str, access_token: str) -> List[Dict[str, Any]]:
    """Fetches all participants for a given PAST webinar from the reporting API."""
    logging.info(f"Fetching all PARTICIPANTS for past webinar {webinar_id} from reports...")
    url = f"https://api.zoom.us/v2/report/webinars/{webinar_id}/participants"
    headers = {"Authorization": f"Bearer {access_token}"}
    participants = _fetch_all_from_zoom(url, headers, "participants")
    logging.info(f"Found {len(participants)} total participants in the report.")
    return participants

# --- GHL Webhook Helper (Unchanged) ---

def send_to_ghl_webhook(contact_data: Dict[str, Any]):
    # ... (This function is unchanged)
    try:
        response = requests.post(GHL_WEBHOOK_URL, json=contact_data)
        response.raise_for_status()
        logging.info(f"Successfully sent {contact_data['email']} to GHL. Attended: {contact_data['attended']}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send data for {contact_data['email']} to GHL: {e}")

# --- Existing Registration Endpoint (Unchanged) ---

@app.post("/register")
async def register_for_webinar(registrant: Registrant):
    # ... (This function is unchanged)
    pass 

# --- REVERTED AND UPDATED: Post-Webinar Processing Endpoint ---

@app.post("/process-attendees")
async def process_webinar_attendees():
    """
    (Robust Method) Fetches webinar registrants and participants from the Reporting API,
    compares them, and sends the status of each registrant to a GHL webhook.
    """
    logging.info("--- Starting Post-Webinar Processing (Robust Reporting Method) ---")

    # 1. Get a fresh access token
    access_token = get_zoom_access_token()
    
    # 2. Fetch both lists from the Zoom Reporting API
    registrants = get_all_past_webinar_registrants_from_report(ZOOM_WEBINAR_ID, access_token)
    participants = get_all_past_webinar_participants_from_report(ZOOM_WEBINAR_ID, access_token)

    if not registrants:
        logging.warning("No registrants found for the webinar. Aborting process.")
        return {"message": "No registrants found. Nothing to process."}

    # 3. Create a set of participant emails for fast lookups
    # Note: The participant report object uses the key 'email', not 'user_email'
    participant_emails = {p["email"].lower() for p in participants}
    
    processed_count = 0
    attended_count = 0
    
    # 4. Iterate through every original registrant
    for registrant in registrants:
        registrant_email = registrant["email"].lower()
        
        attended_status = 1 if registrant_email in participant_emails else 0
        
        if attended_status == 1:
            attended_count += 1
            
        # 5. Prepare and send data to GHL
        contact_payload = {
            "first_name": registrant.get("first_name"),
            "last_name": registrant.get("last_name"),
            "email": registrant.get("email"),
            "attended": attended_status
        }
        send_to_ghl_webhook(contact_payload)
        processed_count += 1
        
    logging.info("--- Post-Webinar Processing Complete ---")
    summary = f"Processed {processed_count} registrants. Attended: {attended_count}. Not Attended: {processed_count - attended_count}."
    logging.info(summary)
    
    return {"message": "Processing complete.", "summary": summary}
# ¤¤¤ End of Code ¤¤¤