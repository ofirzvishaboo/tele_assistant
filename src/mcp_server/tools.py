"""
Google Workspace API integration with retry logic and error handling.

This module provides a production-ready wrapper around Google Calendar, Drive, and Sheets APIs
with comprehensive error handling, retries, and logging.
"""

import datetime
import os.path
import time
from typing import List, Optional, Dict, Any
from functools import wraps
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError
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

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds


def retry_on_error(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    """Decorator to retry function calls on transient errors."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except HttpError as e:
                    last_exception = e
                    # Retry on 5xx errors and rate limits
                    if e.resp.status in [429, 500, 502, 503, 504]:
                        wait_time = delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__} after {wait_time}s: {e}")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__} after {wait_time}s: {e}")
                        time.sleep(wait_time)
                        continue
                    raise
            raise last_exception
        return wrapper
    return decorator


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

    @retry_on_error()
    def calendar_check_availability(self, start_time: str, end_time: str) -> List[Dict[str, Any]]:
        """
        Checks for events in the primary calendar between start_time and end_time.

        Args:
            start_time: ISO 8601 formatted start time
            end_time: ISO 8601 formatted end time

        Returns:
            List of event dictionaries

        Raises:
            HttpError: If API call fails after retries
        """
        logger.info(f"Checking availability from {start_time} to {end_time}")
        try:
            events_result = self.calendar.events().list(
                calendarId='primary',
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            logger.info(f"Found {len(events)} events in time range")
            return events
        except HttpError as e:
            logger.error(f"Google Calendar API error: {e}")
            raise

    @retry_on_error()
    def calendar_create_event(self, summary: str, start_time: str, end_time: str, description: str = "", location: str = "") -> Dict[str, Any]:
        """
        Create a calendar event.

        Args:
            summary: Event title
            start_time: ISO 8601 formatted start time
            end_time: ISO 8601 formatted end time
            description: Event description
            location: Event location

        Returns:
            Created event dictionary with id and htmlLink

        Raises:
            HttpError: If API call fails after retries
        """
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
        except HttpError as e:
            logger.error(f"Failed to create event: {e}")
            raise

    @retry_on_error()
    def drive_create_folder(self, folder_name: str, parent_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a folder in Google Drive.

        Args:
            folder_name: Name of the folder to create
            parent_id: Optional parent folder ID

        Returns:
            Created folder dictionary with id and name

        Raises:
            HttpError: If API call fails after retries
        """
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
        except HttpError as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    @retry_on_error()
    def drive_check_folder_exists(self, folder_id: str) -> bool:
        """
        Check if a folder exists and is accessible by ID.

        Args:
            folder_id: The folder ID to check

        Returns:
            True if folder exists, is accessible, and is actually a folder, False otherwise
        """
        try:
            file_info = self.drive.files().get(
                fileId=folder_id,
                fields="id, name, mimeType, trashed"
            ).execute()

            # Verify it's actually a folder
            mime_type = file_info.get('mimeType', '')
            is_trashed = file_info.get('trashed', False)

            if mime_type != 'application/vnd.google-apps.folder':
                logger.warning(f"File {folder_id} is not a folder (mimeType: {mime_type})")
                return False

            if is_trashed:
                logger.warning(f"Folder {folder_id} is trashed")
                return False

            logger.info(f"Folder {folder_id} exists and is accessible")
            return True
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(f"Folder {folder_id} not found (404)")
                return False
            # For other errors, log and return False
            logger.error(f"Error checking folder {folder_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking folder {folder_id}: {e}")
            return False

    @retry_on_error()
    def drive_list_folders(self, parent_id: Optional[str] = None, page_size: int = 10, name_contains: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List folders in Google Drive.

        Args:
            parent_id: Optional parent folder ID to filter by
            page_size: Maximum number of results to return
            name_contains: Optional exact name match filter

        Returns:
            List of folder dictionaries with id and name

        Raises:
            HttpError: If API call fails after retries
        """
        query = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        if name_contains:
            # Escape single quotes in name
            safe_name = name_contains.replace("'", "\\'")
            query += f" and name = '{safe_name}'"

        try:
            results = self.drive.files().list(
                q=query,
                pageSize=page_size,
                fields="nextPageToken, files(id, name)"
            ).execute()
            folders = results.get('files', [])
            logger.info(f"Found {len(folders)} folders matching criteria")
            return folders
        except HttpError as e:
            logger.error(f"Failed to list folders: {e}")
            raise

    def drive_upload_file(self, filename: str, file_content: bytes, parent_id: str, mime_type: str = 'image/jpeg') -> Dict[str, Any]:
        """
        Upload a file to Google Drive.

        Args:
            filename: Name of the file
            file_content: File content as bytes or bytearray
            parent_id: Parent folder ID
            mime_type: MIME type of the file

        Returns:
            Uploaded file dictionary with id

        Raises:
            HttpError: If API call fails
        """
        logger.info(f"Uploading file '{filename}' to folder '{parent_id}' DRY_RUN={settings.DRY_RUN}")
        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] Uploading file {filename} to {parent_id}")
            return {"id": "dry_run_file_id", "name": filename}

        # Verify folder exists before attempting upload
        try:
            folder_exists = self.drive_check_folder_exists(parent_id)
            if not folder_exists:
                error_msg = f"Folder {parent_id} does not exist or is not accessible"
                logger.error(error_msg)
                # Create a proper HttpError
                from googleapiclient.errors import HttpError as GoogleHttpError
                raise GoogleHttpError(resp={'status': '404'}, content=error_msg)
        except Exception as check_error:
            # If check fails, log but proceed (might be permission issue)
            logger.warning(f"Could not verify folder existence: {check_error}, proceeding with upload")

        # Ensure file_content is bytes (not bytearray)
        if isinstance(file_content, bytearray):
            file_content = bytes(file_content)

        try:
            file_metadata = {'name': filename, 'parents': [parent_id]}
            media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype=mime_type, resumable=True)
            file = self.drive.files().create(body=file_metadata, media_body=media, fields='id').execute()
            logger.info(f"File uploaded successfully: {file.get('id')}")
            return file
        except HttpError as e:
            logger.error(f"Failed to upload file: {e}")
            raise

    @retry_on_error()
    def sheets_append_row(self, values: List[Any]) -> Dict[str, Any]:
        """
        Append a row to the Google Sheet.

        Args:
            values: List of values to append

        Returns:
            Result dictionary with update information

        Raises:
            HttpError: If API call fails after retries
        """
        logger.info(f"Appending row to sheet {settings.GOOGLE_SHEET_ID} DRY_RUN={settings.DRY_RUN}")
        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] Appending row to sheet: {values}")
            return {"updates": {"updatedRows": 1}}

        body = {'values': [values]}
        try:
            result = self.sheets.spreadsheets().values().append(
                spreadsheetId=settings.GOOGLE_SHEET_ID,
                range="A1",
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            logger.info(f"Row appended successfully: {result.get('updates', {}).get('updatedRows', 0)} rows updated")
            return result
        except HttpError as e:
            logger.error(f"Failed to append row: {e}")
            raise

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
