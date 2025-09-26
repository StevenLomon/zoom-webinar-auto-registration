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

# --- NEW: Pydantic Model for Registration Body ---
class WebinarRegistrant(BaseModel):
    """Defines the expected data for a new webinar registrant."""
    email: EmailStr
    first_name: str
    last_name: Optional[str] = None


# --- Zoom Authentication & Fetching Helpers ---

def get_zoom_access_token():
    token_url = "https://zoom.us/oauth/token"
    try:
        response = requests.post(token_url, auth=HTTPBasicAuth(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET), params={"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID})
        response.raise_for_status()
        token_data = response.json()
        logging.info("Successfully obtained Zoom access token.")
        return token_data.get("access_token")
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not authenticate with Zoom: {e}")
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

# --- NEW: Helper Function for Webinar Registration ---
def register_person_for_webinar(webinar_id: str, registrant_data: Dict[str, Any], access_token: str) -> Dict[str, Any]:
    logging.info(f"Attempting to register {registrant_data.get('email')} for webinar {webinar_id}...")
    url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.post(url, headers=headers, json=registrant_data)
        
        # If the request was not successful (not 201 Created), log the specific error from Zoom
        if response.status_code != 201:
            error_details = response.json()
            logging.error(f"Zoom API registration error: {response.status_code} - {error_details}")
            raise HTTPException(status_code=response.status_code, detail=error_details)

        zoom_response_data = response.json()
        logging.info(f"Successfully registered {registrant_data.get('email')}. Join URL created.")
        return zoom_response_data
    except requests.exceptions.RequestException as e:
        logging.error(f"HTTP request failed during Zoom registration: {e}")
        raise HTTPException(status_code=502, detail="Failed to communicate with Zoom for registration.")


def send_to_ghl_webhook(contact_data: Dict[str, Any]):
    try:
        response = requests.post(GHL_WEBHOOK_URL, json=contact_data)
        response.raise_for_status()
        logging.info(f"Successfully sent {contact_data['email']} to GHL webhook.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send data for {contact_data['email']} to GHL: {e}")


# --- API Endpoints ---

@app.post("/register-webinar", status_code=201)
async def register_webinar_attendee(registrant: WebinarRegistrant):
    """
    Registers a new participant for the webinar specified by ZOOM_WEBINAR_ID.
    Accepts a JSON body with email, first_name, and optional last_name.
    """
    logging.info(f"--- Received registration request for {registrant.email} ---")
    access_token = get_zoom_access_token()
    
    try:
        # Convert Pydantic model to dict, excluding fields that were not set (like an optional last_name)
        registrant_payload = registrant.model_dump(exclude_unset=True)
        
        zoom_response = register_person_for_webinar(
            webinar_id=ZOOM_WEBINAR_ID,
            registrant_data=registrant_payload,
            access_token=access_token
        )
        
        summary = f"Successfully registered {registrant.email}."
        logging.info(summary)
        
        return {
            "message": "Registration successful.",
            "registrant_id": zoom_response.get("registrant_id"),
            "join_url": zoom_response.get("join_url")
        }
    except HTTPException as e:
        # Re-raise the exception from the helper function so FastAPI can format the error response
        raise e
    except Exception as e:
        # Catch any other unexpected errors
        logging.error(f"An unexpected error occurred during registration: {e}")
        raise HTTPException(status_code=500, detail="An internal server error occurred.")


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