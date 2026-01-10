from mcp.server.fastmcp import FastMCP
from src.mcp_server import google_service
import logging

# Initialize FastMCP server
mcp = FastMCP("GoogleWorkspace")

@mcp.tool()
def check_calendar_availability(start_time: str, end_time: str) -> str:
    """Check for conflicting events in the calendar."""
    events = google_service.calendar_check_availability(start_time, end_time)
    if not events:
        return "No conflicts found."

    result = "Conflicts found:\n"
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        summary = event.get('summary', 'No Title')
        result += f"- {summary} at {start}\n"
    return result

@mcp.tool()
def create_calendar_event(summary: str, start_time: str, end_time: str, description: str = "") -> str:
    """Create a new event in the calendar."""
    event = google_service.calendar_create_event(summary, start_time, end_time, description)
    return f"Event created: {event.get('htmlLink')}"

@mcp.tool()
def create_drive_folder(folder_name: str, parent_id: str) -> str:
    """Create a new folder in Google Drive."""
    folder = google_service.drive_create_folder(folder_name, parent_id)
    return f"Folder created: {folder.get('name')} (ID: {folder.get('id')})"

@mcp.tool()
def list_drive_folders(parent_id: str) -> str:
    """List folders in Google Drive."""
    folders = google_service.drive_list_folders(parent_id)
    if not folders:
        return "No folders found."
    return "\n".join([f"- {f['name']} (ID: {f['id']})" for f in folders])

@mcp.tool()
def append_sheet_row(values: list[str]) -> str:
    """Append a row to the configured Google Sheet."""
    result = google_service.sheets_append_row(values)
    return f"Row appended. Updated {result.get('updates', {}).get('updatedRows')} rows."

if __name__ == "__main__":
    mcp.run()

