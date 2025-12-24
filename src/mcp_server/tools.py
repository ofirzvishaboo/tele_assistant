import datetime
import os.path
from typing import List, Optional, Dict, Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import logging

from src.config import settings

# Scopes required
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

logger = logging.getLogger(__name__)

class GoogleService:
    def __init__(self):
        self.creds = self._get_credentials()
        self.calendar = build('calendar', 'v3', credentials=self.creds)
        self.drive = build('drive', 'v3', credentials=self.creds)
        self.sheets = build('sheets', 'v4', credentials=self.creds)

    def _get_credentials(self):
        creds = None
        if os.path.exists(settings.GOOGLE_TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(settings.GOOGLE_TOKEN_PATH, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if os.path.exists(settings.GOOGLE_CREDENTIALS_PATH):
                    flow = InstalledAppFlow.from_client_secrets_file(
                        settings.GOOGLE_CREDENTIALS_PATH, SCOPES)
                    # Note: This requires local interaction for the first run
                    # For a headless server, you'd typically copy a generated token.json
                    creds = flow.run_local_server(port=0)
                else:
                    raise FileNotFoundError(f"Credentials file not found at {settings.GOOGLE_CREDENTIALS_PATH}")

            with open(settings.GOOGLE_TOKEN_PATH, 'w') as token:
                token.write(creds.to_json())
        return creds

    def calendar_check_availability(self, start_time: str, end_time: str) -> List[Dict[str, Any]]:
        """
        Checks for events in the primary calendar between start_time and end_time.
        Times should be ISO format strings.
        """
        logger.info(f"--- CHECKING AVAILABILITY ---")
        logger.info(f"Input Start: {start_time}")
        logger.info(f"Input End:   {end_time}")

        events_result = self.calendar.events().list(
            calendarId='primary',
            timeMin=start_time,
            timeMax=end_time,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        logger.info(f"Google API returned {len(events)} events.")
        for i, e in enumerate(events):
             logger.info(f"Event {i}: {e.get('summary')} ({e.get('start').get('dateTime')} - {e.get('end').get('dateTime')})")
        logger.info(f"-----------------------------")

        return events

    def calendar_create_event(self, summary: str, start_time: str, end_time: str, description: str = "", location: str = "") -> Dict[str, Any]:
        logger.info(f"Creating event '{summary}' ({start_time} - {end_time}) DRY_RUN={settings.DRY_RUN}")
        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] Creating event: {summary} ({start_time} - {end_time}) Location: {location}")
            return {"id": "dry_run_event_id", "htmlLink": "http://dry-run"}

        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        if location:
            event['location'] = location
        try:
            event = self.calendar.events().insert(calendarId='primary', body=event).execute()
            logger.info(f"Event created successfully: {event.get('id')}")
            return event
        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            raise e

    def drive_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> Dict[str, Any]:
        logger.info(f"Creating folder '{folder_name}' in parent '{parent_id}' DRY_RUN={settings.DRY_RUN}")
        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] Creating folder: {folder_name} in {parent_id}")
            return {"id": "dry_run_folder_id", "name": folder_name}

        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        try:
            file = self.drive.files().create(body=file_metadata, fields='id, name').execute()
            logger.info(f"Folder created successfully: {file.get('id')}")
            return file
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise e

    def drive_list_folders(self, parent_id: Optional[str] = None, page_size: int = 10, name_contains: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        if name_contains:
            # Escape single quotes in name
            safe_name = name_contains.replace("'", "\\'")
            query += f" and name = '{safe_name}'"

        results = self.drive.files().list(
            q=query,
            pageSize=page_size,
            fields="nextPageToken, files(id, name)"
        ).execute()
        return results.get('files', [])

    def drive_upload_file(self, filename: str, file_content: bytes, parent_id: str, mime_type: str = 'image/jpeg') -> Dict[str, Any]:
        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] Uploading file {filename} to {parent_id}")
            return {"id": "dry_run_file_id", "name": filename}

        file_metadata = {'name': filename, 'parents': [parent_id]}
        media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype=mime_type, resumable=True)
        file = self.drive.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file

    def sheets_append_row(self, values: List[Any]) -> Dict[str, Any]:
        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] Appending row to sheet {settings.GOOGLE_SHEET_ID}: {values}")
            return {"updates": {"updatedRows": 1}}

        body = {'values': [values]}
        try:
            result = self.sheets.spreadsheets().values().append(
                spreadsheetId=settings.GOOGLE_SHEET_ID,
                range="A1",
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            return result
        except Exception as e:
            logger.error(f"Failed to append row: {e}")
            raise e

# Singleton instance
try:
    google_service = GoogleService()
except Exception as e:
    logger.warning(f"Could not initialize GoogleService (might be missing credentials): {e}")
    # Create a dummy for build time / no-creds mode
    class DummyService:
        def __getattr__(self, name):
            def method(*args, **kwargs):
                logger.info(f"[DUMMY SERVICE] Called {name} with {args} {kwargs}")
                return {}
            return method
    google_service = DummyService()
