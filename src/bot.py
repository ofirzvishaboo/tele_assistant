import logging
import asyncio
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from langchain_core.messages import HumanMessage, AIMessage, messages_from_dict, messages_to_dict, SystemMessage
from langchain_openai import ChatOpenAI
from src.config import settings
from src.graph import app as graph_app
from src.db.models import init_db, get_user_state, update_user_state
from src.mcp_server.tools import google_service
import io

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Hello! I am your Event Scheduling Assistant.*\n\n"
        "Here is what I can do for you:\n\n"
        "📅 *Schedule Events*\n"
        "• \"Schedule a meeting with Team on Jan 5th 10:00-11:00\"\n"
        "• \"Book a party for 150 people at Tel Aviv Port tomorrow 18:00-22:00\"\n"
        "_(I will create a Calendar event, a Drive folder, and log it in Sheets)_\n\n"
        "🔍 *Check Schedule*\n"
        "• \"Do I have events tomorrow?\"\n"
        "• \"What is on my calendar for 2025-01-06?\"\n\n"
        "📂 *Manage Files*\n"
        "• Send me a *Photo* to upload it to the active event's folder.\n"
        "• Use /browse to select a different folder for uploads.\n\n"
        "🔄 *Control*\n"
        "• /cancel - Reset the current conversation.",
        parse_mode="Markdown"
    )

async def browse_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recent folders and let user select one."""
    folders = google_service.drive_list_folders(settings.GOOGLE_DRIVE_PARENT_FOLDER_ID)
    if not folders:
        await update.message.reply_text("No folders found.")
        return

    keyboard = []
    for f in folders[:10]: # Limit to 10
        keyboard.append([InlineKeyboardButton(f['name'], callback_data=f"folder_{f['id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a folder to attach photos to:", reply_markup=reply_markup)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    if data.startswith("folder_"):
        folder_id = data.split("_")[1]

        # Update state directly
        user_db_state = await get_user_state(user_id)
        state_dict = user_db_state.conversation_state or {}
        state_dict["folder_selection"] = folder_id

        await update_user_state(user_id, conversation_state=state_dict)
        await query.edit_message_text(text=f"Folder selected (ID: {folder_id}). Future photos will be uploaded here.")

    elif data in ["confirm_yes", "confirm_no"]:
        # Synthesize a message from the user based on the button click
        text = "yes" if data == "confirm_yes" else "no"

        # We process this exactly like a text message
        # Load state, append message, invoke graph
        user_db_state = await get_user_state(user_id)
        current_state = user_db_state.conversation_state

        if not current_state:
            await query.edit_message_text("Session expired. Please start over.")
            return

        if "messages" in current_state and current_state["messages"]:
            try:
                current_state["messages"] = messages_from_dict(current_state["messages"])
            except:
                current_state["messages"] = []

        current_state["messages"].append(HumanMessage(content=text))

        try:
            final_state = await graph_app.ainvoke(current_state)
        except Exception as e:
            logger.error(f"Error invoking graph: {e}", exc_info=True)
            await query.edit_message_text(f"An error occurred: {e}")
            return

        # Respond
        # Get the LAST message from the AI.
        last_msg = final_state["messages"][-1]
        if isinstance(last_msg, AIMessage):
             # Update the message with the result (e.g., "Done! Event created...")
             # This replaces the "Do you want to proceed?" message.
             # Ensure parse_mode is set if we use markdown!
             await query.edit_message_text(last_msg.content, parse_mode="Markdown")

        # Save State
        final_state["messages"] = messages_to_dict(final_state["messages"])
        await update_user_state(user_id, conversation_state=final_state)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Available Commands:*\n\n"
        "/start - Start the bot and see available features\n"
        "/help - Show this help message\n"
        "/template - Get event template (Hebrew format)\n"
        "/browse - Browse and select a Drive folder for uploads\n"
        "/cancel - Reset the current conversation\n\n"
        "💡 *Quick Tips:*\n"
        "• Say \"Schedule a meeting on Jan 5th 10:00-11:00\" to create an event\n"
        "• Ask \"Do I have events tomorrow?\" to check your calendar\n"
        "• Send a photo to upload it to the active event folder",
        parse_mode="Markdown"
    )

async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send static Hebrew event template for easy copy-paste."""
    template_text = """

*שם האירוע:*
*תאריך:*
*שעות:*
*מיקום:*
*אורחים:*
*תיאור:*


*ציוד:*


"""
    await update.message.reply_text(
        template_text,
        parse_mode="Markdown"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Reset state
    empty_state = {
        "messages": [],
        "intent": None,
        "event_details": None,
        "conflicts": None,
        "awaiting_confirmation": False,
        "confirmation_response": None,
        "folder_selection": None,
        "error": None
    }
    await update_user_state(user_id, conversation_state=empty_state)
    await update.message.reply_text("Conversation reset.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_db_state = await get_user_state(user_id)
    state_dict = user_db_state.conversation_state or {}

    folder_id = state_dict.get("folder_selection")
    if not folder_id:
        await update.message.reply_text("No active event folder selected. Please schedule an event first.")
        return

    # Check for photo or video
    if update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
        filename = f"photo_{user_id}_{update.message.message_id}.jpg"
        mime_type = "image/jpeg"
    elif update.message.video:
        file_obj = await update.message.video.get_file()
        # Try to get extension from file_path or default to mp4
        ext = file_obj.file_path.split('.')[-1] if file_obj.file_path else "mp4"
        filename = f"video_{user_id}_{update.message.message_id}.{ext}"
        mime_type = "video/mp4" # Generic
    elif update.message.document:
        file_obj = await update.message.document.get_file()
        filename = update.message.document.file_name or f"doc_{user_id}_{update.message.message_id}"
        mime_type = update.message.document.mime_type or "application/octet-stream"
    else:
        await update.message.reply_text("Unsupported file type.")
        return

    file_byte_array = await file_obj.download_as_bytearray()

    try:
        uploaded = google_service.drive_upload_file(
            filename=filename,
            file_content=file_byte_array,
            parent_id=folder_id,
            mime_type=mime_type
        )
        await update.message.reply_text(f"File uploaded successfully! (ID: {uploaded.get('id')})")
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        await update.message.reply_text(f"Failed to upload file: {e}")

async def handle_general_conversation(text: str) -> str:
    """Handle general conversation using OpenAI when it's not scheduling-related."""
    llm = ChatOpenAI(model="gpt-4o", api_key=settings.OPENAI_API_KEY.get_secret_value())

    system_prompt = """You are a friendly and helpful scheduling assistant.
    You help users schedule events, check their calendar, and manage files.
    Be conversational, friendly, and helpful. If the user asks about scheduling,
    guide them on how to use your features.
    the users can use /browse to select a different folder for uploads.
    the users can use /cancel to reset the current conversation.
    the users can use /start to start a new conversation.
    the users can use /help to get help.
    """

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=text)
    ])

    return response.content

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # 1. Load State
    user_db_state = await get_user_state(user_id)
    current_state = user_db_state.conversation_state

    # If empty or new, init structure
    if not current_state:
        current_state = {
            "messages": [],
            "intent": None,
            "event_details": None,
            "conflicts": None,
            "awaiting_confirmation": False,
            "confirmation_response": None,
            "folder_selection": None,
            "error": None,
            "creating_template": False,
            "template_current_field": None,
            "template_data": {}
        }

    # Deserialize messages if they exist
    if "messages" in current_state and current_state["messages"]:
        # We store them as dicts, need to convert back to objects for LangGraph
        try:
            current_state["messages"] = messages_from_dict(current_state["messages"])
        except:
            current_state["messages"] = []

    # 2. Append User Message
    current_state["messages"].append(HumanMessage(content=text))

    # 3. Invoke Graph to parse intent
    # We pass the state to the graph. The graph determines where to start based on state content.
    try:
        # invoke returns the final state of the run
        final_state = await graph_app.ainvoke(current_state)
    except Exception as e:
        logger.error(f"Error invoking graph: {e}", exc_info=True)
        await update.message.reply_text(f"An error occurred: {e}")
        return

    # 4. Check if this is a general conversation (not scheduling-related)
    intent = final_state.get("intent")

    # If intent is "unknown" or we're not in a scheduling flow, use general conversation
    if intent == "unknown" or (intent not in ["schedule_event", "list_events"] and not final_state.get("awaiting_confirmation")):
        # Use OpenAI for general conversation
        try:
            response_text = await handle_general_conversation(text)
            await update.message.reply_text(response_text)
            # Don't save state for general conversation to avoid polluting the scheduling state
            return
        except Exception as e:
            logger.error(f"Error in general conversation: {e}", exc_info=True)
            await update.message.reply_text("I'm having trouble understanding. Could you try rephrasing?")
            return

    # 5. Respond to User (scheduling-related flow)
    # We look at the latest AIMessage added by the graph
    new_messages = final_state["messages"][len(current_state["messages"]):]
    # Or just get the last message if it's from AI
    last_msg = final_state["messages"][-1]

    # If the graph added a message, send it.
    if isinstance(last_msg, AIMessage):
        content = last_msg.content
        reply_markup = None

        # Check if this is a confirmation question
        is_confirmation = final_state.get("awaiting_confirmation", False)

        if is_confirmation:
            # Use ReplyKeyboard (buttons below chat) using KeyboardButton objects explicitly
            # According to docs: "For simple text buttons, String can be used instead of this object"
            # But we'll use KeyboardButton objects for clarity and future extensibility
            keyboard = [
                [KeyboardButton(text="Yes"), KeyboardButton(text="No")]
            ]
            reply_markup = ReplyKeyboardMarkup(
                keyboard,
                one_time_keyboard=True,  # Hide after one use
                resize_keyboard=True,    # Resize to fit text
                input_field_placeholder="Tap Yes or No"
            )
        else:
            # Clear the keyboard when not asking for confirmation
            reply_markup = ReplyKeyboardRemove()

        await update.message.reply_text(content, reply_markup=reply_markup, parse_mode="Markdown")

    # 6. Save State
    # Serialize messages
    final_state["messages"] = messages_to_dict(final_state["messages"])
    await update_user_state(user_id, conversation_state=final_state)


async def main():
    # Init DB
    await init_db()

    application = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN.get_secret_value()).build()

    # Set bot commands menu
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot and see available features"),
        BotCommand("help", "Get help and see available commands"),
        BotCommand("template", "Get event template (Hebrew format)"),
        BotCommand("browse", "Browse and select a Drive folder for uploads"),
        BotCommand("cancel", "Reset the current conversation")
    ])

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("template", template_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("browse", browse_folders))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    logger.info("Bot is running...")
    # Using start_polling + idle prevents the "already running loop" error
    # when called inside an existing asyncio.run() context
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Block until signal
    # We create a future that never completes effectively
    stop_event = asyncio.Event()
    await stop_event.wait()

    # Shutdown
    await application.updater.stop()
    await application.stop()
    await application.shutdown()

if __name__ == '__main__':
    asyncio.run(main())
