"""
Production-ready Telegram Bot for Event Scheduling.

This module provides the main bot application with comprehensive error handling,
logging, graceful shutdown, and production best practices.
"""

import logging
import asyncio
import signal
import sys
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import TelegramError, NetworkError, TimedOut

from langchain_core.messages import HumanMessage, AIMessage, messages_from_dict, messages_to_dict, SystemMessage
from langchain_openai import ChatOpenAI
from src.config import settings
from src.graph import app as graph_app
from src.db import init_db, get_user_state, update_user_state
from src.mcp_server import google_service
import io

# Configure structured logging
log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]',
    level=log_level,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Global application instance for graceful shutdown
application: Optional[ApplicationBuilder] = None


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler for unhandled exceptions."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    if isinstance(context.error, NetworkError):
        logger.warning("Network error occurred, will retry automatically")
    elif isinstance(context.error, TimedOut):
        logger.warning("Request timed out")
    else:
        # Try to send error message to user if update is available
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Sorry, I encountered an error. Please try again or use /cancel to reset."
                )
            except Exception:
                logger.error("Failed to send error message to user")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    try:
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
    except Exception as e:
        logger.error(f"Error in start command: {e}", exc_info=True)
        raise


async def browse_folders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List recent folders and let user select one."""
    try:
        folders = google_service.drive_list_folders(settings.GOOGLE_DRIVE_PARENT_FOLDER_ID)
        if not folders:
            await update.message.reply_text("No folders found.")
            return

        keyboard = []
        for f in folders[:10]:  # Limit to 10
            keyboard.append([InlineKeyboardButton(f['name'], callback_data=f"folder_{f['id']}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Select a folder to attach photos to:", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in browse_folders: {e}", exc_info=True)
        await update.message.reply_text("Failed to load folders. Please try again later.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button callbacks."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data
    user_id = update.effective_user.id

    try:
        if data.startswith("folder_"):
            logger.info(f"-------------- Data: {data} --------------")
            folder_id = data[7:]  # Remove "folder_" prefix (7 characters)
            logger.info(f"-------------- Folder ID: {folder_id} --------------")

            # Verify folder exists before storing
            try:
                folder_exists = google_service.drive_check_folder_exists(folder_id)
                if not folder_exists:
                    try:
                        await query.edit_message_text(
                            "❌ This folder no longer exists or is not accessible.\n\n"
                            "Please use /browse to select a different folder.",
                            reply_markup=None
                        )
                    except TelegramError as te:
                        if "not modified" not in str(te).lower():
                            logger.warning(f"Telegram error editing message: {te}")
                        await query.answer("Folder no longer exists", show_alert=True)
                    return
            except Exception as e:
                # Only log non-Telegram errors (Google API errors)
                if not isinstance(e, TelegramError):
                    logger.warning(f"Could not verify folder {folder_id} during selection: {e}")
                try:
                    await query.edit_message_text(
                        "⚠️ Could not verify folder access. Please try selecting again or use /browse.",
                        reply_markup=None
                    )
                except TelegramError as te:
                    if "not modified" not in str(te).lower():
                        logger.warning(f"Telegram error editing message: {te}")
                    await query.answer("Could not verify folder", show_alert=True)
                return

            user_db_state = await get_user_state(user_id)
            state_dict = user_db_state.conversation_state or {}
            state_dict["folder_selection"] = folder_id
            await update_user_state(user_id, conversation_state=state_dict)
            try:
                await query.edit_message_text(
                    text=f"✅ Folder selected (ID: {folder_id[:8]}...). Future files will be uploaded here.",
                    reply_markup=None
                )
            except Exception as edit_error:
                # Handle "Message is not modified" error gracefully
                if "not modified" in str(edit_error).lower():
                    await query.answer("Folder already selected", show_alert=False)
                else:
                    raise

        elif data in ["confirm_yes", "confirm_no"]:
            text = "yes" if data == "confirm_yes" else "no"
            user_db_state = await get_user_state(user_id)
            current_state = user_db_state.conversation_state

            if not current_state:
                await query.edit_message_text("Session expired. Please start over.")
                return

            if "messages" in current_state and current_state["messages"]:
                try:
                    current_state["messages"] = messages_from_dict(current_state["messages"])
                except Exception as e:
                    logger.warning(f"Failed to deserialize messages: {e}")
                    current_state["messages"] = []

            current_state["messages"].append(HumanMessage(content=text))

            try:
                final_state = await graph_app.ainvoke(current_state)
            except Exception as e:
                logger.error(f"Error invoking graph: {e}", exc_info=True)
                await query.edit_message_text("An error occurred. Please try again.")
                return

            last_msg = final_state["messages"][-1]
            if isinstance(last_msg, AIMessage):
                await query.edit_message_text(last_msg.content, parse_mode="Markdown")

            final_state["messages"] = messages_to_dict(final_state["messages"])
            await update_user_state(user_id, conversation_state=final_state)
    except Exception as e:
        logger.error(f"Error in handle_callback: {e}", exc_info=True)
        try:
            await query.edit_message_text("An error occurred. Please try again.")
        except Exception:
            pass


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    try:
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
    except Exception as e:
        logger.error(f"Error in help_command: {e}", exc_info=True)


async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send static Hebrew event template for easy copy-paste."""
    template_text = """שם האירוע:



יום הולדת

תאריך:

23.12

שעות:

"10-16"

מיקום:



אורחים:

45

תיאור:

FT

ציוד:

זהב/כסף/נחושת"""
    try:
        await update.message.reply_text(
            template_text,
            parse_mode=None  # Send as plain text for easy copy-paste
        )
    except Exception as e:
        logger.error(f"Error in template_command: {e}", exc_info=True)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cancel command to reset conversation state."""
    try:
        user_id = update.effective_user.id
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
    except Exception as e:
        logger.error(f"Error in cancel: {e}", exc_info=True)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo/video/document uploads."""
    try:
        user_id = update.effective_user.id
        user_db_state = await get_user_state(user_id)
        state_dict = user_db_state.conversation_state or {}

        folder_id = state_dict.get("folder_selection")
        logger.info(f"Current folder_selection from state: {folder_id}")

        if not folder_id:
            await update.message.reply_text("No active event folder selected. Please schedule an event first.")
            return

        # Verify folder exists before proceeding
        logger.info(f"Verifying folder {folder_id} exists before upload...")
        try:
            folder_exists = google_service.drive_check_folder_exists(folder_id)
            if not folder_exists:
                logger.warning(f"Folder {folder_id} does not exist, clearing from state")
                state_dict["folder_selection"] = None
                await update_user_state(user_id, conversation_state=state_dict)
                await update.message.reply_text(
                    "❌ The selected folder no longer exists or is not accessible.\n\n"
                    "Please use /browse to select a new folder or schedule a new event."
                )
                return
        except Exception as e:
            logger.warning(f"Could not verify folder existence: {e}, proceeding with upload attempt")

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
            mime_type = "video/mp4"  # Generic
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
            error_msg = str(e)

            # Handle specific Google Drive errors
            if "404" in error_msg or "not found" in error_msg.lower():
                # Clear invalid folder from state
                state_dict["folder_selection"] = None
                await update_user_state(user_id, conversation_state=state_dict)
                await update.message.reply_text(
                    "❌ The selected folder no longer exists or is not accessible.\n\n"
                    "Please use /browse to select a new folder."
                )
            else:
                await update.message.reply_text(f"Failed to upload file: {error_msg}")
    except Exception as e:
        logger.error(f"Error in handle_photo: {e}", exc_info=True)
        await update.message.reply_text("An error occurred. Please try again.")


async def handle_general_conversation(text: str) -> str:
    """Handle general conversation using OpenAI when it's not scheduling-related."""
    try:
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
    except Exception as e:
        logger.error(f"Error in general conversation: {e}", exc_info=True)
        raise


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages from users."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text

    try:
        # Load State
        user_db_state = await get_user_state(user_id)
        current_state = user_db_state.conversation_state

        # Initialize state if empty
        if not current_state:
            current_state = {
                "messages": [],
                "intent": None,
                "event_details": None,
                "conflicts": None,
                "awaiting_confirmation": False,
                "confirmation_response": None,
                "folder_selection": None,
                "error": None
            }

        # Deserialize messages if they exist
        if "messages" in current_state and current_state["messages"]:
            try:
                current_state["messages"] = messages_from_dict(current_state["messages"])
            except Exception as e:
                logger.warning(f"Failed to deserialize messages: {e}")
                current_state["messages"] = []

        # Append User Message
        current_state["messages"].append(HumanMessage(content=text))

        # Invoke Graph to parse intent
        try:
            final_state = await graph_app.ainvoke(current_state)
        except Exception as e:
            logger.error(f"Error invoking graph: {e}", exc_info=True)
            await update.message.reply_text("An error occurred processing your request. Please try again.")
            return

        # Check if this is a general conversation
        intent = final_state.get("intent")
        if intent == "unknown" or (intent not in ["schedule_event", "list_events"] and not final_state.get("awaiting_confirmation")):
            try:
                response_text = await handle_general_conversation(text)
                await update.message.reply_text(response_text)
                return
            except Exception as e:
                logger.error(f"Error in general conversation: {e}", exc_info=True)
                await update.message.reply_text("I'm having trouble understanding. Could you try rephrasing?")
                return

        # Respond to User (scheduling-related flow)
        last_msg = final_state["messages"][-1]
        if isinstance(last_msg, AIMessage):
            content = last_msg.content
            reply_markup = None
            is_confirmation = final_state.get("awaiting_confirmation", False)

            if is_confirmation:
                keyboard = [[KeyboardButton(text="Yes"), KeyboardButton(text="No")]]
                reply_markup = ReplyKeyboardMarkup(
                    keyboard,
                    one_time_keyboard=True,
                    resize_keyboard=True,
                    input_field_placeholder="Tap Yes or No"
                )
            else:
                reply_markup = ReplyKeyboardRemove()

            await update.message.reply_text(content, reply_markup=reply_markup, parse_mode="Markdown")

        # Save State
        final_state["messages"] = messages_to_dict(final_state["messages"])

        # Log folder_selection if it exists
        if "folder_selection" in final_state and final_state["folder_selection"]:
            logger.info(f"Saving folder_selection to state: {final_state['folder_selection']}")

        await update_user_state(user_id, conversation_state=final_state)
    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        try:
            await update.message.reply_text("An error occurred. Please try again or use /cancel to reset.")
        except Exception:
            pass


def validate_environment() -> None:
    """Validate that all required environment variables are set."""
    required_vars = [
        "TELEGRAM_BOT_TOKEN",
        "OPENAI_API_KEY",
        "GOOGLE_DRIVE_PARENT_FOLDER_ID",
        "GOOGLE_SHEET_ID"
    ]
    missing = []
    for var in required_vars:
        try:
            value = getattr(settings, var)
            if isinstance(value, str) and not value:
                missing.append(var)
        except Exception:
            missing.append(var)

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    logger.info("Environment validation passed")


async def shutdown_handler(signum: int, frame: object) -> None:
    """Handle graceful shutdown on SIGTERM/SIGINT."""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    global application
    if application:
        try:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            logger.info("Application shut down gracefully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
    sys.exit(0)


async def main() -> None:
    """Main entry point for the bot application."""
    global application

    try:
        # Validate environment
        validate_environment()

        # Initialize database
        logger.info("Initializing database...")
        await init_db()
        logger.info("Database initialized")

        # Build application
        logger.info("Building Telegram application...")
        application = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN.get_secret_value()).build()

        # Add global error handler
        application.add_error_handler(error_handler)

        # Set bot commands menu
        await application.bot.set_my_commands([
            BotCommand("start", "Start the bot and see available features"),
            BotCommand("help", "Get help and see available commands"),
            BotCommand("template", "Get event template (Hebrew format)"),
            BotCommand("browse", "Browse and select a Drive folder for uploads"),
            BotCommand("cancel", "Reset the current conversation")
        ])

        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("template", template_command))
        application.add_handler(CommandHandler("cancel", cancel))
        application.add_handler(CommandHandler("browse", browse_folders))
        application.add_handler(CallbackQueryHandler(handle_callback))
        application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_photo))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown_handler(s, f)))
        signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown_handler(s, f)))

        # Start the bot
        logger.info("Starting bot...")
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,  # Ignore old updates on restart
            allowed_updates=["message", "callback_query"]  # Only listen to what we need
        )
        logger.info("Bot is running and ready to receive updates")

        # Keep running until shutdown
        stop_event = asyncio.Event()
        await stop_event.wait()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}", exc_info=True)
        raise
    finally:
        # Ensure cleanup
        if application:
            try:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
            except Exception as e:
                logger.error(f"Error during final cleanup: {e}", exc_info=True)


if __name__ == '__main__':
    asyncio.run(main())
