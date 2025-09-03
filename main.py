import logging
import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from requests.auth import HTTPBasicAuth
from typing import Optional, List, Dict, Any

# --- Basic Configuration ---

# Configure logging to see outputs in the console and a log file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("zoom_and_ghl_processor.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables from .env file for local development
# Render will handle environment variables in production
if os.getenv("RENDER") is None:
    from dotenv import load_dotenv
    load_dotenv()

# --- Load Credentials from Environment ---

# Fetch credentials and webinar ID from environment variables
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_WEBINAR_ID = os.getenv("ZOOM_WEBINAR_ID")
GHL_WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL")

# Validate that all required environment variables are set
if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_WEBINAR_ID, GHL_WEBHOOK_URL]):
    error_message = "One or more required Zoom environment variables are missing."
    logging.error(error_message)
    # This will stop the application from starting if secrets are missing
    raise RuntimeError(error_message)

# --- FastAPI App Initialization ---

app = FastAPI(
    title="Zoom & GHL Integration API",
    description="An API to register users for a Zoom webinar and process attendance post-webinar."
)

# --- Pydantic Data Model ---

# Define the structure and validate the incoming request data
# Zoom API requires first_name and last_name separately
class Registrant(BaseModel):
    first_name: str
    last_name: Optional[str] = None # Now optional, defaults to None
    email: EmailStr

# --- Zoom Authentication Helper ---

def get_zoom_access_token():
    """
    Retrieves an OAuth access token from the Zoom API.
    This token is required to authenticate subsequent API requests.
    """
    token_url = "https://zoom.us/oauth/token"
    try:
        response = requests.post(
            token_url,
            auth=HTTPBasicAuth(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET),
            params={
                "grant_type": "account_credentials",
                "account_id": ZOOM_ACCOUNT_ID,
            }
        )
        response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
        token_data = response.json()
        logging.info("Successfully obtained Zoom access token.")
        return token_data.get("access_token")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to get Zoom access token: {e}")
        # If we can't get a token, we can't proceed.
        raise HTTPException(status_code=500, detail="Could not authenticate with Zoom.")
    
# --- NEW: Zoom Data Fetching Helpers with Pagination ---
def _fetch_all_from_zoom(endpoint_url: str, headers: Dict[str, str], data_key: str) -> List[Dict[str, Any]]:
    """A generic helper to fetch all paginated results from a Zoom endpoint."""
    all_results = []
    params = {"page_size": 300} # Request max page size
    
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
                break # Exit loop if there's no next page
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching data from {endpoint_url}: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch {data_key} from Zoom.")
            
    return all_results

def get_all_webinar_registrants(webinar_id: str, access_token: str) -> List[Dict[str, Any]]:
    """Fetches all registrants for a given webinar, handling pagination."""
    logging.info(f"Fetching all registrants for webinar {webinar_id}...")
    url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants"
    headers = {"Authorization": f"Bearer {access_token}"}
    registrants = _fetch_all_from_zoom(url, headers, "registrants")
    logging.info(f"Found {len(registrants)} total registrants.")
    return registrants

def get_all_webinar_participants(webinar_id: str, access_token: str) -> List[Dict[str, Any]]:
    """Fetches all participants from a past webinar, handling pagination."""
    logging.info(f"Fetching all participants for past webinar {webinar_id}...")
    url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/participants"
    headers = {"Authorization": f"Bearer {access_token}"}
    participants = _fetch_all_from_zoom(url, headers, "participants")
    logging.info(f"Found {len(participants)} total participants.")
    return participants

# --- NEW: GHL Webhook Helper ---

def send_to_ghl_webhook(contact_data: Dict[str, Any]):
    """Sends a single contact's data to the GHL Webhook."""
    try:
        response = requests.post(GHL_WEBHOOK_URL, json=contact_data)
        response.raise_for_status()
        logging.info(f"Successfully sent {contact_data['email']} to GHL webhook. Attended: {contact_data['attended']}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send data for {contact_data['email']} to GHL: {e}")

# --- Auto-Registration Endpoint ---

@app.post("/register")
async def register_for_webinar(registrant: Registrant):
    """
    Receives registrant details and registers them for the Zoom webinar.
    """
    logging.info(f"Received registration request for: {registrant.email}")

    # 1. Get a fresh access token for this request
    access_token = get_zoom_access_token()
    if not access_token:
        # The exception is already raised in the helper, but as a safeguard:
        raise HTTPException(status_code=500, detail="Failed to get Zoom access token.")

    # 2. Prepare the request for Zoom's API
    registration_url = f"https://api.zoom.us/v2/webinars/{ZOOM_WEBINAR_ID}/registrants"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # The data payload must be in JSON format for Zoom
    payload = {
        "email": registrant.email,
        "first_name": registrant.first_name,
        "last_name": registrant.last_name,
    }

    # 3. Send the registration request to Zoom
    try:
        response = requests.post(registration_url, headers=headers, json=payload)

        # Check for specific non-successful status codes
        if response.status_code == 201:  # 201 Created is Zoom's success code
            response_data = response.json()
            logging.info(f"Successfully registered {registrant.email}. Registrant ID: {response_data.get('registrant_id')}")
            return {
                "message": "Contact successfully registered for the webinar.",
                "registrant_id": response_data.get("registrant_id"),
                "join_url": response_data.get("join_url"),
                "webinar_id": response_data.get("id"),
            }
        
        # Handle common errors gracefully
        elif response.status_code == 409: # Conflict - Registrant already exists
            logging.warning(f"Attempted to register an existing user: {registrant.email}")
            raise HTTPException(status_code=409, detail="This email address has already been registered for the webinar.")
        
        else:
            # For all other errors, log the details and return a generic error
            logging.error(f"Zoom API returned an error. Status: {response.status_code}, Response: {response.text}")
            response.raise_for_status() # Raise an exception for other 4xx/5xx errors

    except requests.exceptions.RequestException as e:
        logging.error(f"An error occurred while communicating with the Zoom API: {e}")
        raise HTTPException(status_code=502, detail="An error occurred while communicating with the Zoom API.")

    return {"message": "An unexpected error occurred."} # Fallback

# --- NEW: Post-Webinar Processing Endpoint ---

@app.post("/process-attendees")
async def process_webinar_attendees():
    """
    Fetches webinar registrants and participants, compares them,
    and sends the status of each registrant to a GHL webhook.
    """
    logging.info("--- Starting Post-Webinar Attendee Processing ---")

    # 1. Get a fresh access token
    access_token = get_zoom_access_token()
    
    # 2. Fetch both lists from Zoom
    registrants = get_all_webinar_registrants(ZOOM_WEBINAR_ID, access_token)
    participants = get_all_webinar_participants(ZOOM_WEBINAR_ID, access_token)

    if not registrants:
        logging.warning("No registrants found for the webinar. Aborting process.")
        return {"message": "No registrants found. Nothing to process."}

    # 3. Create a set of participant emails for fast lookups
    # The 'user_email' key is used in the participant list
    participant_emails = {p["user_email"].lower() for p in participants}
    
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