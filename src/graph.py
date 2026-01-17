from typing import Annotated, TypedDict, List, Optional
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from src.config import settings
from src.mcp_server import google_service
from src.cocktail_api import CocktailAPIClient
import json
import logging
import asyncio

logger = logging.getLogger(__name__)

# Define the State
class AgentState(TypedDict):
    messages: List[BaseMessage]
    intent: Optional[str] # "schedule_event", "list_events", "select_folder", "upload_photo", "add_cocktails", "unknown"
    event_details: Optional[dict]
    conflicts: Optional[List[dict]]
    awaiting_confirmation: bool
    confirmation_response: Optional[str]
    folder_selection: Optional[str]
    error: Optional[str]
    proposed_folder_name: Optional[str]
    awaiting_folder_rename: bool
    cocktails: Optional[List[dict]]  # List of cocktails to be served
    awaiting_cocktail_selection: bool  # Waiting for user to specify cocktails
    cocktail_search_results: Optional[List[dict]]  # Multiple matches when name is ambiguous

# --- Tools Wrappers for LLM ---
# Although we have MCP, we use direct function calls here for the LLM to 'bind' to.
# In a full MCP architecture, we'd query the MCP server for tools.
# Here we wrap the local python functions for the LangChain agent.

@tool
def check_calendar(start_time: str, end_time: str):
    """Check availability in calendar."""
    return google_service.calendar_check_availability(start_time, end_time)

@tool
def create_event(summary: str, start_time: str, end_time: str, description: str = "", location: str = ""):
    """Create a calendar event."""
    return google_service.calendar_create_event(summary, start_time, end_time, description, location)

@tool
def create_folder(folder_name: str, parent_id: str):
    """Create a drive folder."""
    return google_service.drive_create_folder(folder_name, parent_id)

@tool
def append_row(values: List[str]):
    """Append row to sheet."""
    return google_service.sheets_append_row(values)

# Initialize Cocktail API client
_cocktail_client: Optional[CocktailAPIClient] = None

def get_cocktail_client() -> CocktailAPIClient:
    """Get or create Cocktail API client."""
    global _cocktail_client
    if _cocktail_client is None:
        _cocktail_client = CocktailAPIClient()
    return _cocktail_client

@tool
async def search_cocktails(query: str) -> List[dict]:
    """Search cocktails by name. Returns list of matching cocktails."""
    try:
        client = get_cocktail_client()
        matches = await client.search_cocktail_by_name(query)
        return [{"id": c["id"], "name": c["name"]} for c in matches]
    except Exception as e:
        logger.error(f"Error searching cocktails: {e}")
        return []

@tool
async def list_cocktails() -> List[dict]:
    """Get all available cocktails. Returns list with id and name."""
    try:
        client = get_cocktail_client()
        cocktails = await client.get_all_cocktails()
        return [{"id": c["id"], "name": c["name"]} for c in cocktails]
    except Exception as e:
        logger.error(f"Error listing cocktails: {e}")
        return []

@tool
async def process_cocktail_order(cocktail_name: str, location: str = "BAR", event_id: Optional[int] = None) -> dict:
    """Process a cocktail order by name. Reduces inventory stock. Returns summary of movements."""
    try:
        client = get_cocktail_client()
        result = await client.process_cocktail_order(
            cocktail_name=cocktail_name,
            location=location,
            event_id=event_id
        )
        return result
    except ValueError as e:
        # Handle multiple matches or not found
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Error processing cocktail order: {e}")
        return {"error": str(e)}

# --- Nodes ---

llm = ChatOpenAI(model="gpt-4o", api_key=settings.OPENAI_API_KEY.get_secret_value())

def parse_request(state: AgentState):
    """
    Analyzes the last message to determine intent and extract entities.
    """
    last_msg = state["messages"][-1]
    if isinstance(last_msg, HumanMessage):
        text = last_msg.content
    else:
        text = str(last_msg)

    # System prompt to guide extraction
    sys_prompt = """
    You are a scheduling assistant. Extract intent and details from the user's message.
    Intents: 'schedule_event', 'list_events', 'select_folder', 'upload_photo', 'add_cocktails', 'unknown'.
    Current time: {current_time}
    Timezone: Asia/Jerusalem (UTC+02:00 or UTC+03:00 depending on DST).

    IMPORTANT: All extracted times MUST be in ISO 8601 format WITH the correct timezone offset for Jerusalem.
    Example: "2025-12-28T10:00:00+02:00" (Winter) or "2025-07-01T10:00:00+03:00" (Summer).
    Do NOT return times in UTC (Z).

    If intent is 'schedule_event', extract:
    - title
    - start_time (ISO 8601 with offset)
    - end_time (ISO 8601 with offset)
    - description
    - location (if mentioned, else empty string)
    - people_count (if mentioned, else empty string)
    - email (if mentioned, else empty string)
    - equipment (if mentioned, else empty string)
    - cocktails (list of cocktail names if mentioned, e.g., ["Margarita", "Mojito"])

    If intent is 'list_events', extract:
    - start_time (ISO 8601, start of the requested period)
    - end_time (ISO 8601, end of the requested period)

    If intent is 'add_cocktails', extract:
    - cocktails (list of cocktail names, e.g., ["Margarita", "Old Fashioned"])

    Return JSON only.
    """.format(current_time=json.dumps(str(datetime.datetime.now())))

    response = llm.invoke([
        SystemMessage(content=sys_prompt),
        HumanMessage(content=text)
    ])

    try:
        # cleanup json
        content = response.content.strip()
        if content.startswith("```json"):
            content = content[7:-3]
        data = json.loads(content)

        # Normalize: if intent is list_events, put details in event_details too for uniformity or a new key
        # For simplicity reusing event_details but checking intent
        result = {
            "intent": data.get("intent"),
            "event_details": data
        }

        # Extract cocktails if present in event_details
        if data.get("cocktails"):
            result["cocktails"] = data.get("cocktails")

        return result
    except Exception as e:
        return {"error": f"Failed to parse: {e}", "intent": "unknown"}

def list_events(state: AgentState):
    details = state["event_details"]
    start = details.get("start_time")
    end = details.get("end_time")

    if not start or not end:
         return {"messages": [AIMessage(content="I couldn't understand the date range to check.")]}

    events = google_service.calendar_check_availability(start, end)

    if not events:
        return {"messages": [AIMessage(content=f"No events found between {start[:10]} and {end[:10]}.")]}

    msg = f"Events for {start[:10]}:\n"
    for event in events:
        start_t = event['start'].get('dateTime', event['start'].get('date'))
        # Parse ISO to HH:MM if possible
        try:
            dt = datetime.datetime.fromisoformat(start_t)
            time_str = dt.strftime("%H:%M")
        except:
            time_str = start_t

        summary = event.get('summary', 'No Title')
        msg += f"- {time_str}: {summary}\n"

    return {"messages": [AIMessage(content=msg)]}

def check_availability(state: AgentState):
    details = state["event_details"]
    if not details:
        return {"error": "No event details found"}

    start = details.get("start_time")
    end = details.get("end_time")

    # Ensure start/end are treated as offsets
    # Even if prompt returns offset, we just pass to Google.

    conflicts = google_service.calendar_check_availability(start, end)

    # NEW: Filter conflicts to ignore the event if it's identical?
    # (Unlikely in this flow, but good to know)

    return {"conflicts": conflicts}

def propose_conflicts_or_confirm(state: AgentState):
    conflicts = state.get("conflicts", [])
    if conflicts:
        msg = "⚠️ *I found conflicts:*\n"
        for c in conflicts:
            # Parse start time
            start_raw = c.get('start', {}).get('dateTime', c.get('start', {}).get('date'))
            end_raw = c.get('end', {}).get('dateTime', c.get('end', {}).get('date'))

            try:
                # Assuming ISO strings like 2025-12-28T10:00:00+02:00
                start_dt = datetime.datetime.fromisoformat(start_raw)
                end_dt = datetime.datetime.fromisoformat(end_raw)
                # Format to HH:MM
                time_range = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
            except:
                time_range = f"{start_raw}"

            msg += f"• {c.get('summary')} ({time_range})\n"

        msg += "\n*Do you want to proceed?*"
        return {
            "messages": [AIMessage(content=msg)],
            "awaiting_confirmation": True
        }
    else:
        title = state['event_details'].get('title')
        return {
            "messages": [AIMessage(content=f"✅ Slot is free.\n\nCreate event *'{title}'*?")],
            "awaiting_confirmation": True
        }

def handle_confirmation(state: AgentState):
    # This node is entered after user replies yes/no
    last_msg = state["messages"][-1].content.lower()
    if "y" in last_msg:
        # Calculate initial proposed name
        details = state["event_details"]
        default_name = f"{details.get('start_time')[:10]} - {details.get('title')}"
        return {
            "confirmation_response": "yes",
            "awaiting_confirmation": False,
            "proposed_folder_name": default_name
        }
    else:
        return {"confirmation_response": "no", "awaiting_confirmation": False}

def process_folder_rename(state: AgentState):
    # User provided a new name
    new_name = state["messages"][-1].content.strip()
    return {
        "proposed_folder_name": new_name,
        "awaiting_folder_rename": False
    }

def check_folder_collision(state: AgentState):
    if state.get("confirmation_response") != "yes":
         return {} # skip

    name = state.get("proposed_folder_name")
    existing = google_service.drive_list_folders(
        parent_id=settings.GOOGLE_DRIVE_PARENT_FOLDER_ID,
        name_contains=name
    )

    if existing:
        return {
            "messages": [AIMessage(content=f"A folder named '{name}' already exists. Please enter a new name for the folder:")],
            "awaiting_folder_rename": True
        }
    return {"awaiting_folder_rename": False}

def handle_cocktail_selection(state: AgentState):
    """Handle cocktail selection and process orders."""
    cocktails = state.get("cocktails", [])
    if not cocktails:
        return {"awaiting_cocktail_selection": False}

    # Run async function in sync context
    client = get_cocktail_client()
    processed_cocktails = []
    errors = []

    # Get event ID if available (from folder_selection or event_details)
    event_id = None
    if state.get("folder_selection"):
        # Use folder ID as event identifier
        event_id = hash(state.get("folder_selection"))

    # Process cocktails synchronously using asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        for cocktail_name in cocktails:
            try:
                result = loop.run_until_complete(
                    client.process_cocktail_order(
                        cocktail_name=cocktail_name,
                        location=settings.COCKTAIL_API_LOCATION,
                        event_id=event_id
                    )
                )
                if "error" in result:
                    errors.append(f"{cocktail_name}: {result['error']}")
                else:
                    processed_cocktails.append(result)
            except Exception as e:
                errors.append(f"{cocktail_name}: {str(e)}")
    finally:
        loop.close()

    if processed_cocktails:
        msg = f"✅ Processed {len(processed_cocktails)} cocktail(s):\n"
        for result in processed_cocktails:
            msg += f"• {result['cocktail']} ({len(result['movements'])} ingredients)\n"
        if errors:
            msg += f"\n⚠️ Errors: {', '.join(errors)}"
        return {
            "messages": [AIMessage(content=msg)],
            "awaiting_cocktail_selection": False,
            "cocktails": None  # Clear after processing
        }
    elif errors:
        return {
            "messages": [AIMessage(content=f"❌ Failed to process cocktails:\n{chr(10).join(errors)}")],
            "awaiting_cocktail_selection": False
        }

    return {"awaiting_cocktail_selection": False}

def commit_actions(state: AgentState):
    if state.get("confirmation_response") != "yes":
        return {"messages": [AIMessage(content="Cancelled.")]}

    details = state["event_details"]
    folder_name = state.get("proposed_folder_name")

    # Format description in Hebrew template format (without location - it goes in location field)
    description_parts = []
    if details.get("title"):
        description_parts.append(f"שם האירוע:\n{details.get('title')}")
    # Location goes in separate field, not in description
    if details.get("people_count"):
        description_parts.append(f"\nאורחים:\n{details.get('people_count')}")
    if details.get("description"):
        description_parts.append(f"\nתיאור:\n{details.get('description')}")
    if details.get("equipment"):
        description_parts.append(f"\nציוד:\n{details.get('equipment')}")

    formatted_description = "\n".join(description_parts) if description_parts else details.get("description", "")

    # 1. Create Event with formatted description and location in separate field
    event = google_service.calendar_create_event(
        details.get("title"),
        details.get("start_time"),
        details.get("end_time"),
        formatted_description,
        location=details.get("location", "")
    )

    # 2. Create Folder
    logger.info(f"Creating folder '{folder_name}' in parent '{settings.GOOGLE_DRIVE_PARENT_FOLDER_ID}'")
    folder = google_service.drive_create_folder(folder_name, settings.GOOGLE_DRIVE_PARENT_FOLDER_ID)
    folder_id = folder.get("id")
    folder_name_returned = folder.get("name")

    if not folder_id:
        logger.error(f"Failed to create folder - no ID returned: {folder}")
        raise ValueError(f"Failed to create folder: {folder}")

    logger.info(f"✅ Created folder successfully:")
    logger.info(f"   Folder ID: {folder_id}")
    logger.info(f"   Folder name: {folder_name_returned}")
    logger.info(f"   Full folder object: {folder}")

    # Verify the folder actually exists
    try:
        folder_verified = google_service.drive_check_folder_exists(folder_id)
        if not folder_verified:
            logger.error(f"⚠️ WARNING: Folder {folder_id} was created but cannot be verified!")
        else:
            logger.info(f"✅ Folder {folder_id} verified and accessible")
    except Exception as verify_error:
        logger.warning(f"Could not verify folder immediately after creation: {verify_error}")

    # 3. Append to Sheet
    # Schema: event_name, people, hours, date, location, email, description, created_at, telegram_user

    # Calculate hours string
    try:
        start_dt = datetime.datetime.fromisoformat(details.get("start_time"))
        end_dt = datetime.datetime.fromisoformat(details.get("end_time"))
        hours_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
    except:
        hours_str = f"{details.get('start_time')} - {details.get('end_time')}"

    row = [
        details.get("title"), # event_name
        details.get("people_count", ""), # people
        hours_str, # hours
        details.get("start_time")[:10], # date
        details.get("location", ""), # location
        details.get("email", ""), # email
        details.get("description", ""), # description
        str(datetime.datetime.now()), # created_at (extra?)
        "telegram_user"
    ]
    google_service.sheets_append_row(row)

    # Process cocktails if any were specified
    cocktails = details.get("cocktails", [])
    cocktail_msg = ""
    if cocktails:
        # Process cocktails synchronously using asyncio
        try:
            client = get_cocktail_client()
            processed = []
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                for cocktail_name in cocktails:
                    try:
                        result = loop.run_until_complete(
                            client.process_cocktail_order(
                                cocktail_name=cocktail_name,
                                location=settings.COCKTAIL_API_LOCATION,
                                event_id=hash(folder_id)  # Use folder ID as event identifier
                            )
                        )
                        if "error" not in result:
                            processed.append(result['cocktail'])
                    except Exception as e:
                        logger.warning(f"Failed to process cocktail {cocktail_name}: {e}")
            finally:
                loop.close()

            if processed:
                cocktail_msg = f"\n\n🍹 Processed {len(processed)} cocktail(s): {', '.join(processed)}"
        except Exception as e:
            logger.error(f"Error processing cocktails: {e}")
            cocktail_msg = f"\n\n⚠️ Could not process cocktails: {str(e)}"

    return {
        "messages": [AIMessage(content=f"Done! Event created. Folder: {folder.get('name')}{cocktail_msg}")],
        "event_details": None, # clear
        "folder_selection": folder_id,  # Save for photo uploads
        "cocktails": None  # Clear after processing
    }

# --- Graph Definition ---
import datetime

workflow = StateGraph(AgentState)

# Nodes
workflow.add_node("parse_request", parse_request)
workflow.add_node("check_calendar", check_availability)
workflow.add_node("list_events", list_events)
workflow.add_node("propose_conflicts", propose_conflicts_or_confirm)
workflow.add_node("process_confirmation", handle_confirmation)
workflow.add_node("process_folder_rename", process_folder_rename)
workflow.add_node("check_folder_collision", check_folder_collision)
workflow.add_node("commit_actions", commit_actions)
workflow.add_node("handle_cocktail_selection", handle_cocktail_selection)

# Entry logic
def route_start(state: AgentState):
    if state.get("awaiting_folder_rename"):
        return "process_folder_rename"
    if state.get("awaiting_confirmation"):
        return "process_confirmation"
    return "parse_request"

workflow.set_conditional_entry_point(
    route_start,
    {
        "process_folder_rename": "process_folder_rename",
        "process_confirmation": "process_confirmation",
        "parse_request": "parse_request"
    }
)

# Conditional edges after parse
def route_after_parse(state):
    intent = state.get("intent")
    if intent == "schedule_event":
        return "check_calendar"
    elif intent == "list_events":
        return "list_events"
    elif intent == "add_cocktails":
        return "handle_cocktail_selection"
    return END

workflow.add_conditional_edges("parse_request", route_after_parse)
workflow.add_edge("check_calendar", "propose_conflicts")
workflow.add_edge("list_events", END)
workflow.add_edge("propose_conflicts", END) # Stops here to wait for user

workflow.add_edge("process_confirmation", "check_folder_collision")
workflow.add_edge("process_folder_rename", "check_folder_collision")

def route_after_collision_check(state):
    if state.get("awaiting_folder_rename"):
        return END # Wait for user input
    return "commit_actions"

workflow.add_conditional_edges("check_folder_collision", route_after_collision_check)
workflow.add_edge("commit_actions", END)
workflow.add_edge("handle_cocktail_selection", END)

app = workflow.compile()

