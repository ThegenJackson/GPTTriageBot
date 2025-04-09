import os
import requests
import openai
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from dotenv import load_dotenv
import traceback



# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    filename='Logs.txt',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'  # This formats the timestamp as HH:MM:SS
)

# Load credentials from .env
openai.api_key = os.getenv("OPENAI_API_KEY")
CW_CLIENT_ID = os.getenv("CW_CLIENT_ID")
CW_COMPANY_ID = os.getenv("CW_COMPANY_ID")
CW_SITE = os.getenv("CW_SITE")

CW_BOARD = "Service"
TIME_WINDOW_MINUTES = 5 

# Define headers with clientId for authentication
HEADERS = {
    "Authorization": f"Bearer {os.getenv('CW_ACCESS_TOKEN')}",  # If using OAuth token, or just set clientId if using legacy method
    "clientId": CW_CLIENT_ID,  # Client ID for the header
    "Content-Type": "application/json",
    "Accept": "application/json"
}



def fetchNewTickets():
    try:
        # Calculate the time cutoff
        time_cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=TIME_WINDOW_MINUTES)
        # Format the timestamp WITHOUT microseconds and WITH 'Z' for UTC
        time_cutoff_str = time_cutoff_dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Prepare the conditions string WITH brackets around the date
        conditions = f"status.name='New' AND board.name='{CW_BOARD}' AND dateEntered>=[{time_cutoff_str}]"
        logging.info(f"Using conditions: {conditions}") # Log the conditions for debugging

        # URL encode the conditions string
        encoded_conditions = quote(conditions)

        # Build the URL with the encoded conditions
        url = f"{CW_SITE}/v4_6_release/apis/3.0/service/tickets?conditions={encoded_conditions}"
        logging.info(f"Requesting URL: {url}") # Log the final URL

        # Send GET request to the ConnectWise API
        response = requests.get(url, headers=HEADERS)

        # Log response status before raising error
        logging.info(f"Response Status Code: {response.status_code}")
        if response.status_code != 200:
             # Log response body for non-200 responses for more detailed errors
            try:
                logging.error(f"Error Response Body: {response.json()}")
            except requests.exceptions.JSONDecodeError:
                logging.error(f"Error Response Body (non-JSON): {response.text}")

        response.raise_for_status()  # Raise error if the request fails (4xx or 5xx)

        # Parse and return the ticket data
        tickets = response.json()
        logging.info(f"Fetched {len(tickets)} ticket(s) from board '{CW_BOARD}' created since {time_cutoff_str}")
        return tickets

    # Catch specific requests exception for better logging
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch tickets due to request error: {e}")
        # Log the traceback for detailed debugging
        logging.error(traceback.format_exc())
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred while fetching tickets: {e}")
        logging.error(traceback.format_exc())
        return []


def getTriageOutput(ticket):
    try:
        system_prompt = (
            "You are a triage analyst reviewing tickets submitted from ConnectWise Manage. "
            "When a ticket is pasted, extract and return a structured analysis in the format below. "
            "The tone must be professional and objective. Do not include casual language, conversational phrasing "
            "(e.g., 'Thanks', 'Here's', 'I've reviewed'), or emojis. Strictly no emojis to be used at all. "
            "The response should be suitable for internal technical documentation. "
            "Explanatory phrasing is required, especially in the Triage Analysis section to justify the verdict. "
            "However, do not use introductory, conversational or casual phrasing such as "
            "'this appears to be', 'here's what I found', or 'let's break this down'. The tone must remain formal and neutral."
        )

        user_prompt = f"""
Title: {ticket['summary']}
Client: {ticket['company']['name']}
Issue: {ticket['initialDescription'] or "Not provided"}
Troubleshooting Steps Taken: None documented
Impact: Unclear from ticket
Urgency/Priority: {ticket['priority']['name']}
Notes:
- Ticket ID: {ticket['id']}

Triage Analysis:
"""

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logging.error(f"Failed to generate GPT triage for ticket #{ticket['id']}: {e}\n{traceback.format_exc()}")
        return "Triage failed: GPT processing error."


def postTicketNote(ticket_id, note_text):
    try:
        url = f"{CW_SITE}/v4_6_release/apis/3.0/service/tickets/{ticket_id}/notes"
        
        # Payload to create the internal note
        note_payload = {
            "text": note_text,
            "detailDescriptionFlag": True,  # Flag for detailed description
            "internalAnalysisFlag": True,  # Ensures it's an internal note
            "resolutionFlag": False  # Ensures it's not marked as a resolution note
        }
        
        # Make the POST request to create the note
        response = requests.post(url, headers=HEADERS, json=note_payload)
        response.raise_for_status()  # Raise error if the request fails (4xx or 5xx)
        
        logging.info(f"Posted internal triage note to ticket #{ticket_id}")

    except Exception as e:
        logging.error(f"Failed to post note to ticket #{ticket_id}: {e}\n{traceback.format_exc()}")



def processTickets():
    tickets = fetchNewTickets()
    for ticket in tickets:
        triage_output = getTriageOutput(ticket)
        postTicketNote(ticket['id'], triage_output)



if __name__ == "__main__":
    logging.info("Starting ConnectWise PA CustomGPT Triage Process")
    processTickets()
    logging.info("Triage Process Completed")
