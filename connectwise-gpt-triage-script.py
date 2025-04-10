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
    datefmt='%H:%M:%S'  # This formats the timestamp as HH:MM:SS
)

company_id = os.getenv("CW_COMPANY_ID")
public_key = os.getenv("CW_PUBLIC_KEY")
private_key = os.getenv("CW_PRIVATE_KEY")
key_string = f"{company_id}+{public_key}:{private_key}"
encoded_key = base64.b64encode(key_string.encode()).decode()
CW_AUTHORIZATION_TOKEN = encoded_key

# Load credentials from .env
client = OpenAI(api_key=f"{os.getenv("OPENAI_API_KEY")}")
CW_CLIENT_ID = os.getenv("CW_CLIENT_ID")
CW_SITE = os.getenv("CW_SITE")
CW_BOARD = "Service"
TIME_WINDOW_MINUTES = 5 

# Define headers with clientId for authentication
HEADERS = {
    "Authorization": f"basic {CW_AUTHORIZATION_TOKEN}",
    "clientId": CW_CLIENT_ID,
    "Content-Type": "application/json",
    "Accept": "application/json"
}


def fetchNewTickets():
    try:
        # Brisbane is UTC+10
        brisbane_offset = timezone(timedelta(hours=10))

        # Now in Brisbane local time
        now_brisbane = datetime.now(brisbane_offset)
        time_cutoff_dt = now_brisbane - timedelta(minutes=TIME_WINDOW_MINUTES)

        # Convert to UTC for ConnectWise API
        time_cutoff_utc = time_cutoff_dt.astimezone(timezone.utc)
        time_cutoff_str = time_cutoff_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Prepare the conditions string WITH brackets around the date
        conditions = f'status/name="New" and board/name="Service" and owner/name=null and contact/name="Thegen Jackson"'

        logging.info(f"Using conditions: {conditions}")  # Log the conditions for debugging

        # URL encode the conditions string
        encoded_conditions = quote(conditions)

        # Build the URL with the encoded conditions
        url = f"{CW_SITE}/v4_6_release/apis/3.0/service/tickets?conditions={encoded_conditions}"
        logging.info(f"Requesting URL: {url}")  # Log the final URL

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
                "Description": full_description  # Full description here
            }

            # Add the ticket data to the list
            tickets_with_notes.append(ticket_data)

        return tickets_with_notes

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
        # Log the ticket structure to debug the issue
        logging.info(f"Ticket data structure: {ticket}")

        # Proceed with existing logic, assuming the ticket structure is correct
        system_prompt = (
            "You are a triage analyst reviewing tickets submitted from ConnectWise Manage. "
            "When a ticket is pasted, extract and return a structured analysis in the format below. "
            "The tone must be formal, objective, and neutral. "
            "The analysis should not include casual language, conversational phrasing (e.g., 'Thanks', 'Here's', 'I've reviewed'), or emojis. "
            "The response should be suitable for internal technical documentation, "
            "focusing on the issue at hand and addressing any non-urgent aspects appropriately. "
            "Urgency should be assessed in a rational manner based on the nature of the request, "
            "with criticality being evaluated relative to the operational environment. "
            "For example, issues like email signature creation should not be categorized as urgent compared to system failures or security issues. "
            "Triage Analysis should be thorough but concise, "
            "offering explanations for verdicts without any informal phrasing or personal commentary. "
            "Explanatory phrasing should focus on factual reasoning and professional justification of decisions. "
            "Avoid introductory statements like 'this appears to be' or 'here’s what I found'. "
            "Ensure that standard elements (e.g., stock images, legal disclaimers) are recognized as routine and non-urgent, "
            "unless specific deviations or complications are stated. "
            "Avoid assuming urgency based solely on customer input, particularly in non-critical cases. "
            "Next Steps or Initial Troubleshooting Recommendations should be provided where applicable. "
            "These steps should be actionable, practical, and based on a logical order of resolution. "
            "They should focus on resolving the issue or providing guidance on how to proceed with further investigation. "
            "Include any relevant resources, tools, or documentation links that may assist in the resolution. "
            "Recommendations should be clear, precise, and professional. "
            "Strictly no emojis to be used at all."
        )

        # Check if the ticket contains the expected fields before accessing them
        ticket_id = ticket.get('id', 'Unknown ID')
        summary = ticket.get('summary', 'No summary available')

        # Log ticket id and summary for debugging
        logging.info(f"Ticket ID: {ticket_id}, Summary: {summary}")

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

        response = client.chat.completions.create(model="gpt-3.5-turbo",  # Replace with the appropriate model
        messages=[{"role": "system", "content": system_prompt},
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