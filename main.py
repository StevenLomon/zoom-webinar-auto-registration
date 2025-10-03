# ¤¤¤ Start of Final Code (Updated for New Documentation) ¤¤¤
import logging
import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from requests.auth import HTTPBasicAuth
from typing import Optional, List, Dict, Any

# --- Basic Configuration (Unchanged) ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("zoom_and_ghl_processor.log"), logging.StreamHandler()])
if os.getenv("RENDER") is None:
    from dotenv import load_dotenv
    load_dotenv()

# --- Load Credentials (Unchanged) ---
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_WEBINAR_ID = os.getenv("ZOOM_WEBINAR_ID")
GHL_WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL")
if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_WEBINAR_ID, GHL_WEBHOOK_URL]):
    raise RuntimeError("One or more required environment variables are missing.")

app = FastAPI(title="Zoom & GHL Integration API")

# --- Pydantic Model (Unchanged) ---
class WebinarRegistrant(BaseModel):
    email: EmailStr
    first_name: str
    last_name: Optional[str] = None

# --- Zoom Authentication & Helpers ---

def get_zoom_access_token(): # Unchanged
    token_url = "https://zoom.us/oauth/token"
    try:
        response = requests.post(token_url, auth=HTTPBasicAuth(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET), params={"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID})
        response.raise_for_status()
        token_data = response.json()
        logging.info("Successfully obtained Zoom access token.")
        return token_data.get("access_token")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail="Could not authenticate with Zoom.")

def _fetch_all_from_zoom(endpoint_url: str, headers: Dict[str, str], data_key: str) -> List[Dict[str, Any]]: # Unchanged
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
            raise HTTPException(status_code=502, detail=f"Failed to fetch {data_key} from Zoom.")
    return all_results

# UPDATED: Function to get participants aligned with new documentation
def get_all_past_webinar_participants(webinar_id: str, access_token: str) -> List[Dict[str, Any]]:
    logging.info(f"Fetching all PARTICIPANTS for past webinar {webinar_id}...")
    # UPDATED: The endpoint URL now uses /past_webinars/ instead of /report/webinars/
    url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/participants"
    headers = {"Authorization": f"Bearer {access_token}"}
    participants = _fetch_all_from_zoom(url, headers, "participants")
    logging.info(f"Found {len(participants)} total participants.")
    return participants

def get_all_webinar_registrants(webinar_id: str, access_token: str) -> List[Dict[str, Any]]: # Unchanged
    logging.info(f"Fetching all REGISTRANTS for webinar {webinar_id}...")
    url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants"
    headers = {"Authorization": f"Bearer {access_token}"}
    registrants = _fetch_all_from_zoom(url, headers, "registrants")
    logging.info(f"Found {len(registrants)} total registrants.")
    return registrants

# --- Unchanged GHL and Registration Helpers ---
def register_person_for_webinar(webinar_id: str, registrant_data: Dict[str, Any], access_token: str) -> Dict[str, Any]: # Unchanged
    logging.info(f"Attempting to register {registrant_data.get('email')} for webinar {webinar_id}...")
    url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.post(url, headers=headers, json=registrant_data)
        if response.status_code != 201:
            error_details = response.json()
            raise HTTPException(status_code=response.status_code, detail=error_details)
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail="Failed to communicate with Zoom for registration.")

def send_to_ghl_webhook(contact_data: Dict[str, Any]): # Unchanged
    try:
        response = requests.post(GHL_WEBHOOK_URL, json=contact_data)
        response.raise_for_status()
        logging.info(f"Successfully sent {contact_data['email']} to GHL webhook.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send data for {contact_data['email']} to GHL: {e}")

# --- API Endpoints ---
@app.post("/register-webinar", status_code=201)
async def register_webinar_attendee(registrant: WebinarRegistrant): # Unchanged
    access_token = get_zoom_access_token()
    registrant_payload = registrant.model_dump(exclude_unset=True)
    zoom_response = register_person_for_webinar(
        webinar_id=ZOOM_WEBINAR_ID, registrant_data=registrant_payload, access_token=access_token
    )
    return {"message": "Registration successful.", "registrant_id": zoom_response.get("registrant_id"), "join_url": zoom_response.get("join_url")}

# UPDATED: Main processing endpoint with corrected logic
@app.post("/process-registrants")
async def process_all_registrants():
    logging.info("--- Starting Full Post-Webinar Segmentation Processing ---")
    access_token = get_zoom_access_token()
    
    # 1. Get both lists from Zoom
    all_registrants = get_all_webinar_registrants(ZOOM_WEBINAR_ID, access_token)
    all_participants = get_all_past_webinar_participants(ZOOM_WEBINAR_ID, access_token) # UPDATED: Calls the renamed function
    
    # 2. Create a set of participant emails for fast lookups
    # UPDATED: The email field in the participants list is 'user_email'
    participant_emails = {person['user_email'] for person in all_participants if 'user_email' in person}
    
    # 3. Process every registrant (Logic is unchanged)
    attended_count = 0
    noshow_count = 0
    for registrant in all_registrants:
        email = registrant.get("email")
        if not email:
            continue

        status = 1 if email in participant_emails else 0
        
        if status == 1:
            attended_count += 1
        else:
            noshow_count += 1
            
        contact_payload = {
            "first_name": registrant.get("first_name"),
            "last_name": registrant.get("last_name", ""),
            "email": email,
            "attended": status
        }
        send_to_ghl_webhook(contact_payload)
        
    summary = f"Processing complete. Sent {attended_count} attendees and {noshow_count} no-shows to GHL."
    logging.info(summary)
    
    return {"message": "Full segmentation processing complete.", "summary": summary}
# ¤¤¤ End of Final Code (Updated for New Documentation) ¤¤¤