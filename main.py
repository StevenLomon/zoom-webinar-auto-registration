import logging
import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from requests.auth import HTTPBasicAuth

# --- Basic Configuration ---

# Configure logging to see outputs in the console and a log file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("zoom_auto_register.log"),
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

# Validate that all required environment variables are set
if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_WEBINAR_ID]):
    error_message = "One or more required Zoom environment variables are missing."
    logging.error(error_message)
    # This will stop the application from starting if secrets are missing
    raise RuntimeError(error_message)

# --- FastAPI App Initialization ---

app = FastAPI(
    title="Zoom Auto-Registration API",
    description="An API to automatically register users for a specific Zoom webinar."
)

# --- Pydantic Data Model ---

# Define the structure and validate the incoming request data
# Zoom API requires first_name and last_name separately
class Registrant(BaseModel):
    first_name: str
    last_name: str
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

# --- API Endpoint ---

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