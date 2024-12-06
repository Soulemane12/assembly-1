import os
import json
import datetime
import re  # Ensure this import exists
from dotenv import load_dotenv
import assemblyai as aai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pytz  # For timezone handling
import dateparser  # For parsing natural language dates

# Load environment variables from .env file
load_dotenv()

# AssemblyAI Setup
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not ASSEMBLYAI_API_KEY:
    raise ValueError("AssemblyAI API key not set. Please check your .env file.")

aai.settings.api_key = ASSEMBLYAI_API_KEY
transcriber = aai.Transcriber()

# Google Calendar API Setup
SCOPES = ['https://www.googleapis.com/auth/calendar']
GOOGLE_CLIENT_SECRET_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
if not GOOGLE_CLIENT_SECRET_FILE:
    raise ValueError("Google client secret file path not set. Please check your .env file.")

def authenticate_google_calendar():
    """Authenticate and return the Google Calendar service."""
    creds = None
    # Token file stores the user's access and refresh tokens
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If no valid credentials, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CLIENT_SECRET_FILE, SCOPES
            )
            creds = flow.run_local_server(port=8080)
        # Save the credentials for next time
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('calendar', 'v3', credentials=creds)
    return service

def transcribe_audio(audio_file_path):
    """
    Transcribes an audio file from a local path using AssemblyAI.
    """
    try:
        print("Transcribing audio...")
        transcript = transcriber.transcribe(audio_file_path)
        transcript = transcript.wait_for_completion()
        if transcript.status == aai.TranscriptStatus.error:
            print(f"Transcription failed: {transcript.error}")
            return None
        return transcript
    except Exception as e:
        print(f"Transcription error: {e}")
        return None
def extract_task_details(transcript_text):
    try:
        print(f"Transcript Text: {transcript_text}")  # Log the transcript to understand the input

        task = re.search(r"(?:schedule|book|arrange) a meeting with (.+?) at (.+?)(?:\.|$)", transcript_text, re.IGNORECASE)
        
        if task:
            with_whom = task.group(1).strip()
            time_str = task.group(2).strip()

            time_parsed = dateparser.parse(time_str, settings={'RETURN_AS_TIMEZONE_AWARE': True})
            if not time_parsed:
                print("Failed to parse time.")
                return {}
            time_iso = time_parsed.isoformat()

            return {
                "task": f"Meeting with {with_whom}",
                "with_whom": with_whom,
                "date_time": time_iso
            }
        else:
            print("Failed to extract task details using regex.")
            return {}

    except Exception as e:
        print(f"Error extracting task details: {e}")
        return {}


def ask_follow_up_questions(task_details):
    """
    Identifies missing fields and asks the user for additional details.
    """
    required_fields = {
        "task": {
            "question": "1. **Event Title**\nPlease provide a clear and concise title for the event (e.g., 'Team Meeting', 'Client Call').",
            "mandatory": True
        },
        "date_time": {
            "question": "2. **Date & Time**\nPlease specify the date and time for the event (e.g., 'December 7, 2024, 3:00 PM EST').",
            "mandatory": True
        },
        "location": {
            "question": "3. **Location**\nPlease provide the location of the event. If it's virtual, include the meeting link and access details.",
            "mandatory": False
        },
        "description": {
            "question": "4. **Description**\nProvide a brief description of the event’s purpose, agenda, or goals.",
            "mandatory": False
        },
        "participants": {
            "question": "5. **Participants/Attendees**\nList who is invited to the event, including names and roles.",
            "mandatory": False
        },
        "attachments": {
            "question": "6. **Attachments/Links**\nInclude any necessary files or links to relevant resources (e.g., project files, articles).",
            "mandatory": False
        },
        "recurrence": {
            "question": "7. **Recurrence**\nWill this event repeat? If yes, specify the pattern (daily, weekly, monthly, etc.) and any exceptions.",
            "mandatory": False
        },
        "notes": {
            "question": "8. **Notes/Additional Information**\nAny additional details, such as parking information, special instructions, or pre-event preparation.",
            "mandatory": False
        },
        "rsvp": {
            "question": "9. **Action Items/RSVP Requests**\nDoes the event require attendees to RSVP or complete specific tasks beforehand? If yes, provide instructions.",
            "mandatory": False
        }
    }

    print("\nTo create a detailed calendar event, please provide additional information where needed.\n")

    for field, details in required_fields.items():
        # If the field is already present and not empty, skip
        if field in task_details and task_details[field]:
            continue
        # Ask the question
        response = input(details["question"] + "\nYour Answer: ").strip()
        if response:
            task_details[field] = response
        elif details["mandatory"]:
            # Re-ask if the field is mandatory and not provided
            while not response and details["mandatory"]:
                response = input("This field is mandatory. " + details["question"] + "\nYour Answer: ").strip()
                if response:
                    task_details[field] = response
                    break
    return task_details

def parse_time(time_str):
    """
    Parses time string into a timezone-aware datetime object.
    """
    try:
        dt = dateparser.parse(time_str, settings={'RETURN_AS_TIMEZONE_AWARE': True})
        if not dt:
            raise ValueError("Unable to parse the provided date and time.")
        return dt
    except Exception as e:
        print(f"Time parsing error: {e}")
        raise

def create_calendar_event(service, task_details):
    """
    Constructs and adds a detailed calendar event to Google Calendar.
    """
    try:
        # Extract and validate required fields
        title = task_details.get("task", "No Title Provided")
        date_time_str = task_details.get("date_time", None)
        if not date_time_str:
            print("Event date and time not provided.")
            return

        # Parse date and time with timezone
        start_time = parse_time(date_time_str)
        # Assuming a default duration of 1 hour if end time not provided
        end_time = start_time + datetime.timedelta(hours=1)

        # Prepare the event dictionary
        event = {
            'summary': title,
            'description': task_details.get("description", ""),
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': str(start_time.tzinfo) if start_time.tzinfo else 'UTC',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': str(end_time.tzinfo) if end_time.tzinfo else 'UTC',
            },
            'location': task_details.get("location", ""),
            'attendees': [],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 24 hours before
                    {'method': 'popup', 'minutes': 30},       # 30 minutes before
                ],
            },
        }

        # Add participants if provided
        participants = task_details.get("participants", "")
        if participants:
            # Assuming participants are comma-separated emails or names
            attendees = [{"email": email.strip()} for email in participants.split(",") if "@" in email]
            # If emails are not provided, skip adding attendees
            if attendees:
                event['attendees'] = attendees

        # Add attachments/links if provided
        attachments = task_details.get("attachments", "")
        if attachments:
            event['description'] += f"\nAttachments/Links: {attachments}"

        # Add notes/additional information if provided
        notes = task_details.get("notes", "")
        if notes:
            event['description'] += f"\nNotes: {notes}"

        # Add recurrence if provided
        recurrence = task_details.get("recurrence", "")
        if recurrence:
            # Placeholder for actual RRULE parsing based on user input
            # Example for weekly recurrence:
            event['recurrence'] = [
                "RRULE:FREQ=WEEKLY;COUNT=10"
            ]

        # Add RSVP requests if provided
        rsvp = task_details.get("rsvp", "")
        if rsvp:
            event['description'] += f"\nRSVP: {rsvp}"

        # Insert the event into Google Calendar
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        print(f"\nEvent created successfully: {created_event.get('htmlLink')}")
    except Exception as e:
        print(f"Error adding event to calendar: {e}")

def display_upcoming_events(service, max_results=10):
    """
    Displays upcoming events from Google Calendar.
    """
    try:
        print("\nYour Upcoming Events:")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        if not events:
            print("No upcoming events found.")
            return

        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            print(f"- {summary} at {start}")
    except Exception as e:
        print(f"Error fetching upcoming events: {e}")

def main(audio_file_path):
    # Step 1: Transcribe audio
    transcript_obj = transcribe_audio(audio_file_path)
    if not transcript_obj:
        print("Failed to transcribe audio.")
        return

    transcript_text = transcript_obj.text
    print(f"\nTranscript:\n{transcript_text}")

    # Step 2: Extract task details using basic parsing
    task_details = extract_task_details(transcript_text)
    if not task_details:
        print("Failed to extract task details.")
        return

    print("\nExtracted Task Details:")
    print(json.dumps(task_details, indent=2))

    # Step 3: Ask follow-up questions for more details
    task_details = ask_follow_up_questions(task_details)

    print("\nFinal Task Details:")
    print(json.dumps(task_details, indent=2))

    # Step 4: Authenticate with Google Calendar
    service = authenticate_google_calendar()

    # Step 5: Add event to calendar with detailed information
    create_calendar_event(service, task_details)

    # Step 6: Display updated schedule
    display_upcoming_events(service)

if __name__ == "__main__":
    # Example Usage
    # Replace 'audio.wav' with the actual path to your local audio file
    audio_file_path = "audio.wav"
    main(audio_file_path)
