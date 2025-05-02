import os
import requests
from openai import OpenAI
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from dotenv import load_dotenv
import base64
import traceback

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    filename='Logs.txt',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

# Open AI Key, Model and Prompt
client = OpenAI(api_key=f"{os.getenv("OPENAI_API_KEY")}")
GPT_MODEL = os.getenv("GPT_MODEL")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT")

# ConnectWise PSA Keys and Authentication
company_id = os.getenv("CW_COMPANY_ID")
public_key = os.getenv("CW_PUBLIC_KEY")
private_key = os.getenv("CW_PRIVATE_KEY")
key_string = f"{company_id}+{public_key}:{private_key}"
encoded_key = base64.b64encode(key_string.encode()).decode()
CW_AUTHORIZATION_TOKEN = encoded_key

# ConnectWise PSA Connection
CW_CLIENT_ID = os.getenv("CW_CLIENT_ID")
CW_SITE = os.getenv("CW_SITE")

# ConnectWise PSA Integration Details
CW_BOARD = os.getenv("CW_BOARD")
CW_STATUS = os.getenv("CW_STATUS")
TIME_WINDOW = os.getenv("TIME_WINDOW")
LOCAL_TIMEZONE = os.getenv("LOCAL_UTC_OFFEST")

# Define headers with clientId for authentication
HEADERS = {
    "Authorization": f"basic {CW_AUTHORIZATION_TOKEN}",
    "clientId": CW_CLIENT_ID,
    "Content-Type": "application/json",
    "Accept": "application/json"
}


def fetchNewTickets():
    try:
        # Offest UTC based on local timezone
        local_timezone_offset = timezone(timedelta(hours=LOCAL_TIMEZONE))

        # Now in local time
        local_timezone_now = datetime.now(local_timezone_offset)
        time_cutoff_dt = local_timezone_now - timedelta(minutes=TIME_WINDOW)

        # Convert to UTC for ConnectWise API
        time_cutoff_utc = time_cutoff_dt.astimezone(timezone.utc)
        time_cutoff_str = time_cutoff_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Prepare the conditions string WITH brackets around the date
        conditions = f'status/name={CW_STATUS} and board/name={CW_BOARD} and owner/name=null and FIX DATE' #FIX DATE

        # URL encode the conditions string
        encoded_conditions = quote(conditions)

        # Build the URL with the encoded conditions
        url = f"{CW_SITE}/v4_6_release/apis/3.0/service/tickets?conditions={encoded_conditions}"

        # Send GET request to the ConnectWise API
        response = requests.get(url, headers=HEADERS)

        if response.status_code != 200:
            # Log response body for non-200 responses for more detailed errors
            try:
                logging.error(f"Error Response Body: {response.json()}")
            except requests.exceptions.JSONDecodeError:
                logging.error(f"Error Response Body (non-JSON): {response.text}")

        response.raise_for_status()  # Raise error if the request fails (4xx or 5xx)

        # Parse and return the ticket data
        tickets = response.json()
        logging.info(f"Fetched {len(tickets)} ticket(s) from '{CW_BOARD}' board created since {time_cutoff_str}")

        # Create a list to hold the final ticket data with notes included
        tickets_with_notes = []

        for ticket in tickets:
            ticket_id = ticket['id']
            summary = ticket['summary']
            description = ticket.get('description', "Description not available")  # Fallback if no description

            # Fetch notes using the notes_href from the ticket
            notes_url = ticket['_info'].get('notes_href') if '_info' in ticket else None
            if notes_url:
                notes_response = requests.get(notes_url, headers=HEADERS)
                if notes_response.status_code == 200:
                    notes = notes_response.json()
                    note_texts = [note['text'] for note in notes]
                    full_description = " ".join(note_texts) if note_texts else "No notes available"
                else:
                    full_description = "Error fetching notes"
            else:
                full_description = "No notes URL available"

            # Prepare the ticket data with notes
            ticket_data = {
                "Ticket ID": ticket_id,
                "Summary": summary,
                "Description": full_description
            }

            # Add the ticket data to the list
            tickets_with_notes.append(ticket_data)

        return tickets_with_notes

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch tickets due to request error: {e}")
        logging.error(traceback.format_exc())
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred while fetching tickets: {e}")
        logging.error(traceback.format_exc())
        return []


def getTriageOutput(ticket):
    try:
        # Check if the ticket contains the expected fields before accessing them
        ticket_id = ticket.get('id', 'Unknown ID')
        summary = ticket.get('summary', 'No summary available')

        # Use the full description which now includes both description and notes
        full_description = ticket.get('Description', 'No description available')

        user_prompt = f"""
Title: {summary}
Client: {ticket.get('Company', {}).get('Name', 'Unknown Company')}
Issue: {full_description}
Troubleshooting Steps Taken: None documented
Impact: Unclear from ticket
Urgency/Priority: {ticket.get('Priority', {}).get('Name', 'Unknown')}
Notes:
- Ticket ID: {ticket_id}

Triage Analysis:
"""

        response = client.chat.completions.create(model=GPT_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user_prompt}])

        return response.choices[0].message.content.strip()

    except KeyError as e:
        logging.error(f"KeyError: Missing key {e} in ticket data: {ticket}")
        return "Triage failed: Missing required ticket data."
    except Exception as e:
        logging.error(f"Failed to generate GPT triage for ticket: {e}\n{traceback.format_exc()}")
        return "Triage failed: GPT processing error."



def postTicketNote(ticket_id, note_text):
    try:
        url = f"{CW_SITE}/v4_6_release/apis/3.0/service/tickets/{ticket_id}/notes"

        # Payload to create the internal note
        note_payload = {
            "text": note_text,
            "detailDescriptionFlag": False,  # Flag for detailed description
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
        # Get triage output from GPT for the current ticket
        triage_output = getTriageOutput(ticket)

        # Post the GPT response as an internal note to ConnectWise
        postTicketNote(ticket['id'], triage_output)


if __name__ == "__main__":
    logging.info("Starting ConnectWise PA CustomGPT Triage Process")
    processTickets()
    logging.info("Triage Process Completed")