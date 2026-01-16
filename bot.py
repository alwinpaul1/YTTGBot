"""A Telegram bot to download YouTube videos as MP3 files."""

import asyncio  # For rate limiting progress updates if needed
import logging
import os
import re

import httpx
import telegram
import yt_dlp
from dotenv import load_dotenv
from pyrogram import Client as PyrogramClient
from pyrogram.errors import (
    FloodWait,
)  # For handling flood wait errors with Pyrogram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables from .env file
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

# Enable logging - Set to DEBUG to capture more from yt-dlp
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

# Regex to find YouTube URLs (including Shorts)
YOUTUBE_URL_REGEX = (
    r"(?:https?:\/\/)?(?:www\.)?"
    r"(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)([\w\-]+)"
)

# This dictionary will store the last update time for each chat_id to
# limit edits.
progress_message_last_edit_time = {}

# Store pending video URLs for quality selection (video_id -> url)
pending_video_urls = {}


# --- Simplified Inline Keyboard ---
def get_info_inline_keyboard() -> InlineKeyboardMarkup:
    """Return a simple inline keyboard with instructions to send a link."""
    keyboard = [
        [
            InlineKeyboardButton(
                "📝 How to Send a Link", callback_data="show_link_instructions"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_format_selection_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """Return inline keyboard to choose between audio or video download."""
    keyboard = [
        [
            InlineKeyboardButton(
                "🎵 Download Audio (MP3)", callback_data=f"download_audio:{video_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🎬 Download Video", callback_data=f"download_video:{video_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel", callback_data=f"cancel:{video_id}"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def get_available_qualities(url: str) -> list[dict]:
    """
    Fetch available video qualities from YouTube.
    Returns a list of dicts with height and label.
    Uses player clients that don't require authentication.
    """
    # Try different player clients that work without authentication
    ydl_opts_list = [
        {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "extractor_args": {
                "youtube": {"player_client": ["ios", "web"]}
            },
        },
        {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "extractor_args": {
                "youtube": {"player_client": ["android_creator", "web"]}
            },
        },
        {
            "quiet": True, 
            "no_warnings": True,
            "extract_flat": False,
        },
    ]
    
    for ydl_opts in ydl_opts_list:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    continue
                
                formats = info.get("formats", [])
                available_qualities = []
                seen_labels = set()
                
                # Get all video formats with height info
                video_formats = [
                    f for f in formats 
                    if f.get("vcodec") != "none" and f.get("height")
                ]
                video_formats.sort(key=lambda x: x.get("height", 0), reverse=True)
                
                # Height ranges for widescreen videos (2.39:1 aspect ratio)
                # Maps actual heights to standard quality labels
                def get_quality_label(height):
                    if height >= 1400:  # 1440p+ (actual 1074-1610)
                        if height >= 2000 or height >= 1500:  # Very tall = 4K
                            return (2160, "2160p 4K")
                        return (1440, "1440p 2K")
                    elif height >= 700:  # 720p-1080p (actual 536-806)
                        if height >= 800:
                            return (1080, "1080p HD")
                        return (720, "720p HD")
                    elif height >= 350:  # 480p (actual 358)
                        return (480, "480p")
                    elif height >= 250:  # 360p (actual 268)
                        return (360, "360p")
                    elif height >= 170:  # 240p (actual 178)
                        return (240, "240p")
                    else:  # 144p (actual 128)
                        return (144, "144p")
                
                for fmt in video_formats:
                    height = fmt.get("height")
                    if height:
                        quality_height, label = get_quality_label(height)
                        if label not in seen_labels:
                            seen_labels.add(label)
                            available_qualities.append({
                                "height": quality_height,
                                "label": label,
                            })
                
                # Sort by height descending
                available_qualities.sort(key=lambda x: x["height"], reverse=True)
                
                if available_qualities:
                    logger.info(f"Found {len(available_qualities)} quality options for {url}")
                    return available_qualities
                    
        except Exception as e:
            logger.warning(f"Error fetching qualities with strategy: {e}")
            continue
    
    # If all methods fail, return common quality options  
    # The download function will handle getting the closest available quality
    logger.warning(f"Using default quality options for {url}")
    return [
        {"height": 1080, "label": "1080p HD"},
        {"height": 720, "label": "720p HD"},
        {"height": 480, "label": "480p"},
        {"height": 360, "label": "360p"},
    ]


def get_video_quality_keyboard(video_id: str, qualities: list[dict]) -> InlineKeyboardMarkup:
    """Create inline keyboard with available video quality options."""
    keyboard = []
    
    for quality in qualities:
        height = quality["height"]
        label = quality["label"]
        keyboard.append([
            InlineKeyboardButton(
                f"📹 {label}", callback_data=f"quality:{video_id}:{height}"
            )
        ])
    
    # Add back button
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data=f"back_to_format:{video_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)


# Progress hook for yt-dlp
def ytdl_progress_hook(d):
    """Log the progress of the yt-dlp download."""
    if d["status"] == "finished":
        final_filename = d.get("info_dict", {}).get("filepath") or d.get(
            "filename"
        )
        actual_ext = d.get("info_dict", {}).get("ext")
        logger.info(
            "yt-dlp hook: Finished processing. Expected final file: "
            f"{final_filename}, Actual format/ext reported by yt-dlp: "
            f"{actual_ext}"
        )
    elif d["status"] == "error":
        logger.error(f"yt-dlp hook: Error. Data: {d}")
    else:
        # Simplified logging for other statuses
        logger.debug(f"yt-dlp hook: status {d['status']}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with the inline button."""
    logger.info(
        "START command initiated by user "
        f"{update.effective_user.id if update.effective_user else 'N/A'}"
    )
    await update.message.reply_text(
        "Hi! 🎬 I can download YouTube videos as **audio (MP3)** or **video** files.\n\n"
        "Simply paste a YouTube video link and send it to me!",
        parse_mode="Markdown",
        reply_markup=get_info_inline_keyboard(),  # Show the single button
    )


# --- Simplified Callback Query Handler ---
async def button_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle button presses from the inline keyboard."""
    # Direct print for immediate feedback
    print("BUTTON_CALLBACK_HANDLER_ENTERED_VIA_PRINT")
    logger.info("--- button_callback_handler: Entered function VIA LOGGER ---")
    query = update.callback_query

    if not query:
        print("BUTTON_CALLBACK_HANDLER_QUERY_IS_NONE_VIA_PRINT")
        logger.error("button_callback_handler: query object is None.")
        return

    print(
        "BUTTON_CALLBACK_HANDLER_QUERY_OBJECT_VALID_VIA_PRINT: "
        f"id={query.id}, data={query.data}"
    )
    logger.debug(
        f"button_callback_handler: query object found: id={query.id}, "
        f"data={query.data}"
    )

    try:
        print(
            "BUTTON_CALLBACK_HANDLER_ATTEMPTING_ANSWER_VIA_PRINT: "
            f"data={query.data}"
        )
        logger.info(
            "button_callback_handler: Attempting to answer query ID: "
            f"{query.id} with data: {query.data}"
        )
        await query.answer()  # Acknowledge the button press
        print(
            f"BUTTON_CALLBACK_HANDLER_ANSWERED_QUERY_VIA_PRINT: "
            f"data={query.data}"
        )
        logger.info(
            "button_callback_handler: Successfully answered query ID: "
            f"{query.id} with data: {query.data}"
        )

        if query.data == "show_link_instructions":
            print("BUTTON_CALLBACK_HANDLER_IF_BRANCH_ENTERED_VIA_PRINT")
            logger.info("'show_link_instructions' button pressed.")
            instruction_text = (
                "To send a link: Just copy the full YouTube video URL "
                "(e.g., from your browser or the YouTube app) and paste "
                "it directly into our chat, then send it as a message."
            )
            try:
                await query.edit_message_text(
                    text=instruction_text,
                    reply_markup=get_info_inline_keyboard(),
                )
                msg_id = query.message.message_id if query.message else "N/A"
                logger.info(f"Edited message {msg_id} with instructions.")
            except telegram.error.BadRequest as e_edit:
                if "message is not modified" in str(e_edit).lower():
                    msg_id = (
                        query.message.message_id if query.message else "N/A"
                    )
                    logger.debug(
                        f"Instruction message {msg_id} already shown."
                    )
                else:
                    msg_id = (
                        query.message.message_id if query.message else "N/A"
                    )
                    logger.error(
                        f"Failed to edit message {msg_id} for instructions: "
                        f"{e_edit}. Sending new message."
                    )
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=instruction_text,
                        reply_markup=get_info_inline_keyboard(),
                    )
            except Exception as e_edit_generic:
                msg_id = query.message.message_id if query.message else "N/A"
                logger.error(
                    f"Generic error editing message {msg_id} for "
                    f"instructions: {e_edit_generic}. Sending new."
                )
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=instruction_text,
                    reply_markup=get_info_inline_keyboard(),
                )
        
        # Handle audio download request
        elif query.data.startswith("download_audio:"):
            video_id = query.data.split(":")[1]
            youtube_url = pending_video_urls.get(video_id)
            
            if not youtube_url:
                await query.edit_message_text(
                    "❌ Session expired. Please send the YouTube link again."
                )
                return
            
            chat_id = query.message.chat_id
            logger.info(f"Audio download requested for video_id: {video_id}")
            
            # Process audio download
            await process_audio_download(
                chat_id, video_id, youtube_url, context, query
            )
            
            # Clean up pending URL
            pending_video_urls.pop(video_id, None)
        
        # Handle video download request - show quality selection
        elif query.data.startswith("download_video:"):
            video_id = query.data.split(":")[1]
            youtube_url = pending_video_urls.get(video_id)
            
            if not youtube_url:
                await query.edit_message_text(
                    "❌ Session expired. Please send the YouTube link again."
                )
                return
            
            logger.info(f"Video download requested for video_id: {video_id}")
            
            # Show loading message while fetching qualities
            await query.edit_message_text("🔍 Fetching available qualities...")
            
            # Get available qualities
            qualities = await get_available_qualities(youtube_url)
            
            if qualities:
                await query.edit_message_text(
                    "📹 **Select Video Quality:**",
                    parse_mode="Markdown",
                    reply_markup=get_video_quality_keyboard(video_id, qualities),
                )
            else:
                # Fallback to default qualities if fetch fails
                default_qualities = [
                    {"height": 720, "label": "720p HD"},
                    {"height": 480, "label": "480p"},
                    {"height": 360, "label": "360p"},
                ]
                await query.edit_message_text(
                    "📹 **Select Video Quality:**\n_(Couldn't detect available qualities)_",
                    parse_mode="Markdown",
                    reply_markup=get_video_quality_keyboard(video_id, default_qualities),
                )
        
        # Handle quality selection
        elif query.data.startswith("quality:"):
            parts = query.data.split(":")
            video_id = parts[1]
            height = int(parts[2])
            youtube_url = pending_video_urls.get(video_id)
            
            if not youtube_url:
                await query.edit_message_text(
                    "❌ Session expired. Please send the YouTube link again."
                )
                return
            
            chat_id = query.message.chat_id
            logger.info(f"Video download at {height}p requested for video_id: {video_id}")
            
            # Process video download
            await process_video_download(
                chat_id, video_id, youtube_url, height, context, query
            )
            
            # Clean up pending URL
            pending_video_urls.pop(video_id, None)
        
        # Handle back button
        elif query.data.startswith("back_to_format:"):
            video_id = query.data.split(":")[1]
            
            if video_id not in pending_video_urls:
                await query.edit_message_text(
                    "❌ Session expired. Please send the YouTube link again."
                )
                return
            
            await query.edit_message_text(
                "🎬 **YouTube Link Detected!**\n\n"
                "Choose download format:",
                parse_mode="Markdown",
                reply_markup=get_format_selection_keyboard(video_id),
            )
        
        # Handle cancel button
        elif query.data.startswith("cancel:"):
            video_id = query.data.split(":")[1]
            
            # Clean up pending URL
            pending_video_urls.pop(video_id, None)
            
            # Show the welcome/start menu
            await query.edit_message_text(
                "Hi! 🎬 I can download YouTube videos as **audio (MP3)** or **video** files.\n\n"
                "Simply paste a YouTube video link and send it to me!",
                parse_mode="Markdown",
                reply_markup=get_info_inline_keyboard(),
            )
        
        else:
            logger.warning(f"Unknown callback_data received: '{query.data}'")

    except Exception as e_main_cb:
        print(f"BUTTON_CALLBACK_HANDLER_ERROR_VIA_PRINT: {e_main_cb}")
        logger.error(
            f"Error in button_callback_handler: {e_main_cb}", exc_info=True
        )
        if query and query.message:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        "Sorry, there was an error processing that button "
                        "tap."
                    ),
                )
            except Exception:
                pass


async def pyrogram_upload_progress(
    current, total, chat_id, message_id, ptb_bot_instance
):
    """Pyrogram progress callback to update upload status."""
    percentage = int((current / total) * 100)
    now = asyncio.get_event_loop().time()
    last_edit_time = progress_message_last_edit_time.get(
        (chat_id, message_id), 0
    )

    if now - last_edit_time > 1.0:  # Edit at most once per second
        try:
            await ptb_bot_instance.edit_message_text(
                text=f"Uploading large audio: {percentage}%",
                chat_id=chat_id,
                message_id=message_id,
            )
            progress_message_last_edit_time[(chat_id, message_id)] = now
            logger.debug(
                f"Pyrogram Upload Progress: {percentage}% for chat "
                f"{chat_id}, msg {message_id}"
            )
        except telegram.error.BadRequest as e:
            if "message is not modified" in str(e).lower():
                logger.debug(
                    f"Progress message {message_id} already at "
                    f"{percentage}%."
                )
            else:
                logger.warning(
                    f"Failed to edit progress message {message_id} for "
                    f"Pyrogram upload: {e}"
                )
        except Exception as e:
            logger.warning(
                f"Generic error editing progress message {message_id} for "
                f"Pyrogram upload: {e}"
            )


async def send_audio_with_pyrogram(
    chat_id: int,
    file_path: str,
    ptb_bot_instance,
    processing_message_ptb: Update | None,
    caption: str | None = None,
) -> bool:
    """Send an audio file using Pyrogram, suitable for larger files."""
    if not API_ID or not API_HASH:
        logger.error(
            "Pyrogram API_ID or API_HASH not configured. Cannot send "
            "large file."
        )
        return False

    pyrogram_session_name = "pyrogram_bot_session"

    progress_args_tuple = None
    # Ensure attributes exist
    if (
        processing_message_ptb
        and ptb_bot_instance
        and hasattr(processing_message_ptb, "chat_id")
        and hasattr(processing_message_ptb, "message_id")
    ):
        progress_args_tuple = (
            processing_message_ptb.chat_id,
            processing_message_ptb.message_id,
            ptb_bot_instance,
        )
    else:
        logger.warning(
            "Cannot set up Pyrogram progress reporting: "
            "processing_message_ptb is None or lacks chat_id/message_id."
        )

    try:
        effective_api_id = 0
        try:
            effective_api_id = int(API_ID)
        except (ValueError, TypeError):
            logger.error(f"Invalid API_ID: '{API_ID}'. Must be an integer.")
            return False

        async with PyrogramClient(
            name=pyrogram_session_name,
            api_id=effective_api_id,
            api_hash=API_HASH,
            bot_token=TELEGRAM_BOT_TOKEN,
            in_memory=True,
        ) as app:
            logger.info(
                f"Pyrogram client sending audio: {file_path} to {chat_id} "
                "with progress."
            )
            await app.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption or "",
                progress=pyrogram_upload_progress
                if progress_args_tuple
                else None,
                progress_args=progress_args_tuple
                if progress_args_tuple
                else (),
            )
            logger.info(
                f"Pyrogram successfully sent audio: {file_path} to {chat_id}"
            )
            if progress_args_tuple:
                progress_message_last_edit_time.pop(
                    (progress_args_tuple[0], progress_args_tuple[1]), None
                )
            return True
    except FloodWait as e_flood:
        logger.error(
            f"Pyrogram FloodWait: Must wait {e_flood.value} seconds before "
            f"sending to {chat_id}."
        )
        if progress_args_tuple:
            progress_message_last_edit_time.pop(
                (progress_args_tuple[0], progress_args_tuple[1]), None
            )
        return False
    except Exception as e:
        logger.error(f"Error sending audio with Pyrogram: {e}", exc_info=True)
        if progress_args_tuple:
            progress_message_last_edit_time.pop(
                (progress_args_tuple[0], progress_args_tuple[1]), None
            )
        return False


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle incoming messages, primarily for YouTube links."""
    message_text = update.message.text
    # Ignore if no text or if it's a callback_query being processed
    if not message_text or update.callback_query:
        return

    logger.debug(f"handle_message received text: '{message_text[:50]}...'")

    match = re.search(YOUTUBE_URL_REGEX, message_text)
    if match:
        youtube_url = match.group(0)
        video_id = match.group(1)
        chat_id = update.message.chat_id
        logger.info(
            f"Received YouTube URL: {youtube_url} from chat_id: {chat_id}"
        )

        # Store the URL for later use in callbacks
        pending_video_urls[video_id] = youtube_url
        
        # Show format selection keyboard
        await update.message.reply_text(
            "🎬 **YouTube Link Detected!**\n\n"
            "Choose download format:",
            parse_mode="Markdown",
            reply_markup=get_format_selection_keyboard(video_id),
        )
        return

    # Non-YouTube message handling
    else:
        logger.debug(
            f"Non-URL message from "
            f"{update.effective_user.id if update.effective_user else 'N/A'}"
            f": '{message_text[:50]}...'"
        )
        await update.message.reply_text(
            "Please send a YouTube video link. Tap button for how-to.",
            reply_markup=get_info_inline_keyboard(),
        )


async def process_audio_download(
    chat_id: int,
    video_id: str,
    youtube_url: str,
    context: ContextTypes.DEFAULT_TYPE,
    query,
) -> None:
    """Process audio download request from callback."""
    processing_message = None
    audio_file_path = None
    audio_sent_successfully = False
    
    try:
        # Edit the original message to show processing status
        try:
            await query.edit_message_text("⏳ Processing audio, please wait...")
            processing_message = query.message
        except Exception as e:
            logger.warning(f"Could not edit message: {e}")
        
        audio_file_path = await download_and_convert_youtube(youtube_url, video_id)
        
        if audio_file_path:
            file_size = os.path.getsize(audio_file_path)
            logger.info(f"Audio file created: {audio_file_path}, Size: {file_size} bytes")
            
            TELEGRAM_AUDIO_LIMIT_BYTES = 50 * 1024 * 1024
            
            if file_size > TELEGRAM_AUDIO_LIMIT_BYTES:
                logger.info(f"File size {file_size // (1024*1024)}MB > PTB limit. Using Pyrogram.")
                
                if processing_message:
                    try:
                        await processing_message.edit_text(
                            "📤 Audio is large, uploading with alternative method..."
                        )
                    except Exception:
                        pass
                
                pyrogram_sent = await send_audio_with_pyrogram(
                    chat_id,
                    audio_file_path,
                    context.bot,
                    processing_message,
                    caption=f"🎵 Audio from YouTube",
                )
                
                if pyrogram_sent:
                    audio_sent_successfully = True
                    if processing_message:
                        try:
                            await processing_message.delete()
                        except Exception:
                            pass
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="✅ Large audio sent! Send another link to download.",
                        reply_markup=get_info_inline_keyboard(),
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Failed to send large audio file.",
                        reply_markup=get_info_inline_keyboard(),
                    )
            else:
                # File is small enough for PTB
                logger.info(f"Sending audio via PTB: {audio_file_path}")
                try:
                    title_without_ext = os.path.splitext(os.path.basename(audio_file_path))[0]
                    with open(audio_file_path, "rb") as audio_file_obj:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=audio_file_obj,
                            filename=os.path.basename(audio_file_path),
                            title=title_without_ext,
                            read_timeout=180,
                            write_timeout=180,
                            connect_timeout=180,
                        )
                    audio_sent_successfully = True
                    if processing_message:
                        try:
                            await processing_message.delete()
                        except Exception:
                            pass
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="✅ Audio sent! Send another link to download.",
                        reply_markup=get_info_inline_keyboard(),
                    )
                except Exception as e_send:
                    logger.error(f"Failed to send audio: {e_send}", exc_info=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Error sending audio: {e_send}",
                        reply_markup=get_info_inline_keyboard(),
                    )
        else:
            logger.warning(f"download_and_convert_youtube returned None for {youtube_url}")
            if processing_message:
                try:
                    await processing_message.edit_text(
                        "❌ Couldn't process YouTube link. Try again later."
                    )
                except Exception:
                    pass
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Couldn't process YouTube link. Try again later.",
                    reply_markup=get_info_inline_keyboard(),
                )
    
    except Exception as e:
        logger.error(f"Error in process_audio_download: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ An error occurred: {e}",
            reply_markup=get_info_inline_keyboard(),
        )
    
    finally:
        # Cleanup temp file
        if audio_file_path and os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
                logger.info(f"Removed temp file: {audio_file_path}")
            except OSError as e:
                logger.error(f"Error removing temp file: {e}")


async def process_video_download(
    chat_id: int,
    video_id: str,
    youtube_url: str,
    height: int,
    context: ContextTypes.DEFAULT_TYPE,
    query,
) -> None:
    """Process video download request at specified quality."""
    processing_message = None
    video_file_path = None
    video_sent_successfully = False
    
    try:
        # Edit the original message to show processing status
        try:
            await query.edit_message_text(f"⏳ Downloading video at {height}p, please wait...")
            processing_message = query.message
        except Exception as e:
            logger.warning(f"Could not edit message: {e}")
        
        video_file_path = await download_youtube_video(youtube_url, video_id, height)
        
        if video_file_path:
            file_size = os.path.getsize(video_file_path)
            file_size_mb = file_size // (1024 * 1024)
            logger.info(f"Video file created: {video_file_path}, Size: {file_size_mb}MB")
            
            # Telegram limit is 2GB for bots
            TELEGRAM_VIDEO_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
            TELEGRAM_PTB_LIMIT_BYTES = 50 * 1024 * 1024
            
            if file_size > TELEGRAM_VIDEO_LIMIT_BYTES:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Video is too large ({file_size_mb}MB). Telegram limit is 2GB.",
                    reply_markup=get_info_inline_keyboard(),
                )
                return
            
            if file_size > TELEGRAM_PTB_LIMIT_BYTES:
                logger.info(f"Video size {file_size_mb}MB > 50MB. Using Pyrogram.")
                
                if processing_message:
                    try:
                        await processing_message.edit_text(
                            f"📤 Uploading video ({file_size_mb}MB)..."
                        )
                    except Exception:
                        pass
                
                pyrogram_sent = await send_video_with_pyrogram(
                    chat_id,
                    video_file_path,
                    context.bot,
                    processing_message,
                    caption=f"🎬 {height}p video from YouTube",
                )
                
                if pyrogram_sent:
                    video_sent_successfully = True
                    if processing_message:
                        try:
                            await processing_message.delete()
                        except Exception:
                            pass
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="✅ Video sent! Send another link to download.",
                        reply_markup=get_info_inline_keyboard(),
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Failed to send video file.",
                        reply_markup=get_info_inline_keyboard(),
                    )
            else:
                # File is small enough for PTB
                logger.info(f"Sending video via PTB: {video_file_path}")
                try:
                    with open(video_file_path, "rb") as video_file_obj:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=video_file_obj,
                            filename=os.path.basename(video_file_path),
                            caption=f"🎬 {height}p video",
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=180,
                            supports_streaming=True,
                        )
                    video_sent_successfully = True
                    if processing_message:
                        try:
                            await processing_message.delete()
                        except Exception:
                            pass
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="✅ Video sent! Send another link to download.",
                        reply_markup=get_info_inline_keyboard(),
                    )
                except Exception as e_send:
                    logger.error(f"Failed to send video: {e_send}", exc_info=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Error sending video: {e_send}",
                        reply_markup=get_info_inline_keyboard(),
                    )
        else:
            logger.warning(f"download_youtube_video returned None for {youtube_url}")
            if processing_message:
                try:
                    await processing_message.edit_text(
                        "❌ Couldn't download video. Try a different quality or try again later."
                    )
                except Exception:
                    pass
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Couldn't download video. Try again later.",
                    reply_markup=get_info_inline_keyboard(),
                )
    
    except Exception as e:
        logger.error(f"Error in process_video_download: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ An error occurred: {e}",
            reply_markup=get_info_inline_keyboard(),
        )
    
    finally:
        # Cleanup temp file
        if video_file_path and os.path.exists(video_file_path):
            try:
                os.remove(video_file_path)
                logger.info(f"Removed temp video file: {video_file_path}")
            except OSError as e:
                logger.error(f"Error removing temp video file: {e}")


def sanitize_filename(title: str, max_length: int = 200) -> str:
    """
    Sanitize a video title to create a safe filename.
    Keeps spaces but removes/replaces problematic characters.
    """
    # Characters that are not allowed in filenames
    invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']

    # Replace invalid characters with nothing or space
    sanitized = title
    for char in invalid_chars:
        sanitized = sanitized.replace(char, '')

    # Replace multiple spaces with single space
    sanitized = ' '.join(sanitized.split())

    # Trim to max length (leaving room for .mp3 extension)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].strip()

    return sanitized


async def download_and_convert_youtube(url: str, video_id: str) -> str | None:
    """
    Download a YouTube video and convert it to an MP3 file.

    This function uses yt-dlp to download a video from a given URL and
    FFmpeg to convert it into an MP3 audio file. It tries multiple
    strategies to ensure the download is successful.
    """
    # Download with video_id first, then rename to title
    base_output_template = f"{video_id}"
    temp_final_path = f"{base_output_template}.mp3"
    expected_final_path = None

    # Optimized strategy - using the proven working method
    # (Firefox cookies + TV client)
    download_strategies = [
        # Primary strategy: Firefox cookies with TV client (proven to work)
        {
            "name": "firefox_cookies_tv_client",
            "opts": {
                "cookiesfrombrowser": ("firefox",),
                "format": "bestaudio/best[height<=480]/worst",
                "extractor_args": {
                    "youtube": {"player_client": ["tv", "web"]}
                },
            },
        },
        # Fallback strategy: Basic approach without cookies
        {
            "name": "basic_fallback",
            "opts": {
                "format": "bestaudio/best[height<=480]/worst",
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web"],
                        "player_skip": ["webpage"],
                        "formats": "missing_pot",
                    }
                },
                "ignoreerrors": True,
            },
        },
    ]

    base_ydl_opts = {
        # yt-dlp saves as this, FFmpeg adds .mp3
        "outtmpl": base_output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                # Use 192kbps instead of 0 for more consistent results
                "preferredquality": "192",
            }
        ],
        "noplaylist": True,
        "logger": logger,
        "verbose": True,
        "noprogress": True,
        "ignoreerrors": False,
        "progress_hooks": [ytdl_progress_hook],
        # More robust user agent
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
        },
        # Additional options to help with extraction
        "extract_flat": False,
        "age_limit": None,
        "geo_bypass": True,
        # Make sure FFmpeg location is either in PATH or specified here
        # 'ffmpeg_location': '/path/to/your/ffmpeg',
    }

    # Try downloading with different strategies
    for i, strategy in enumerate(download_strategies):
        try:
            # Create ydl_opts for this strategy
            ydl_opts = base_ydl_opts.copy()
            ydl_opts.update(strategy["opts"])

            logger.info(
                f"Attempting download with strategy '{strategy['name']}' for: "
                f"{url}. Base output: {base_output_template}"
            )

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # Get the video title and sanitize it
                if info:
                    video_title = info.get('title', video_id)
                    sanitized_title = sanitize_filename(video_title)
                    expected_final_path = f"{sanitized_title}.mp3"

            logger.info(
                f"yt-dlp download & FFmpeg conversion for {url} completed "
                f"using strategy '{strategy['name']}'."
            )

            # Check if the temp file exists and rename it
            if os.path.exists(temp_final_path):
                # Rename from video_id.mp3 to sanitized_title.mp3
                try:
                    os.rename(temp_final_path, expected_final_path)
                    logger.info(
                        f"Renamed file from {temp_final_path} to "
                        f"{expected_final_path}"
                    )
                except OSError as e_rename:
                    logger.warning(
                        f"Could not rename file: {e_rename}. "
                        f"Using original filename."
                    )
                    expected_final_path = temp_final_path

            if expected_final_path and os.path.exists(expected_final_path):
                logger.info(
                    "Conversion successful. MP3 file created: "
                    f"{expected_final_path}"
                )
                return expected_final_path
            else:
                logger.error(
                    "CRITICAL: No MP3 file was found after FFmpeg "
                    f"processing for {url}."
                )

                try:
                    current_dir_files = os.listdir(".")
                    logger.error(
                        "Files in current directory ('.') to help debug: "
                        f"{current_dir_files}"
                    )
                except Exception as e_ls:
                    logger.error(f"Could not list current directory: {e_ls}")

                # If this isn't the last strategy, continue to the next one
                if i < len(download_strategies) - 1:
                    continue
                else:
                    return None

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logger.warning(
                f"yt-dlp DownloadError with strategy '{strategy['name']}' "
                f"for {url}: {e}"
            )

            # Check for specific error types and decide whether to continue
            if i < len(download_strategies) - 1:  # Not the last strategy
                if (
                    "Sign in to confirm you're not a bot" in error_msg
                    or "DPAPI" in error_msg
                    or "Failed to decrypt" in error_msg
                    or "failed to load cookies" in error_msg.lower()
                    or "could not find" in error_msg.lower()
                    or "Invalid po_token" in error_msg
                    or "Requested format is not available" in error_msg
                    or "Signature extraction failed" in error_msg
                    or "HTTP Error 403" in error_msg
                    or "forbidden" in error_msg.lower()
                    or "unable to download video data" in error_msg
                ):
                    logger.info(
                        f"Strategy '{strategy['name']}' failed with known "
                        "issue, trying next strategy..."
                    )
                    continue

            # If it's the last strategy, handle cleanup and return None
            logger.error(
                f"Final yt-dlp DownloadError (tried {i+1} strategies) "
                f"for {url}: {e}",
                exc_info=True,
            )
            if expected_final_path and os.path.exists(expected_final_path):
                os.remove(expected_final_path)
            return None

        except Exception as e:
            logger.warning(
                f"General error with strategy '{strategy['name']}' "
                f"for {url}: {e}"
            )
            # If we have more strategies to try, continue
            if i < len(download_strategies) - 1:
                logger.info(
                    f"Strategy '{strategy['name']}' failed, trying next "
                    "strategy..."
                )
                continue

            # Last strategy failed, clean up and return None
            logger.error(
                f"Final general error (tried {i+1} strategies) for {url}: "
                f"{e}",
                exc_info=True,
            )
            if expected_final_path and os.path.exists(expected_final_path):
                os.remove(expected_final_path)
            return None

    # If we get here, all strategies failed
    logger.error(f"All download strategies failed for {url}")
    return None


async def download_youtube_video(url: str, video_id: str, height: int) -> str | None:
    """
    Download a YouTube video at the specified quality.
    
    Downloads video and audio separately and merges them using FFmpeg.
    """
    base_output_template = f"{video_id}_video"
    expected_final_path = None
    
    # Map standard heights to actual height thresholds for widescreen videos
    # The format selector uses actual pixel heights, not standard labels
    height_map = {
        2160: 1400,  # 4K videos have actual height ~1610
        1440: 1000,  # 1440p videos have actual height ~1074
        1080: 700,   # 1080p videos have actual height ~806
        720: 500,    # 720p videos have actual height ~536
        480: 320,    # 480p videos have actual height ~358
        360: 220,    # 360p videos have actual height ~268
        240: 150,    # 240p videos have actual height ~178
        144: 100,    # 144p videos have actual height ~128
    }
    
    actual_height = height_map.get(height, height)
    
    # Format string to get video at or above the actual height threshold + best audio
    # Using 'bestvideo' with height filter to get the best quality at or above our threshold
    format_string = f"bestvideo[height>={actual_height}]+bestaudio/bestvideo+bestaudio/best"
    
    ydl_opts = {
        "outtmpl": base_output_template + ".%(ext)s",
        "format": format_string,
        "merge_output_format": "mp4",
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
        "noplaylist": True,
        "logger": logger,
        "verbose": True,
        "noprogress": True,
        "ignoreerrors": False,
        "progress_hooks": [ytdl_progress_hook],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "geo_bypass": True,
    }
    
    # Use default yt-dlp - works for all formats including 4K
    # No special player clients needed, they cause issues with authentication
    
    try:
        logger.info(f"Downloading video at {height}p for: {url}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info:
                video_title = info.get('title', video_id)
                sanitized_title = sanitize_filename(video_title)
                
                # Find the downloaded file
                temp_file = f"{base_output_template}.mp4"
                expected_final_path = f"{sanitized_title}_{height}p.mp4"
                
                if os.path.exists(temp_file):
                    try:
                        os.rename(temp_file, expected_final_path)
                        logger.info(f"Renamed to: {expected_final_path}")
                    except OSError:
                        expected_final_path = temp_file
                
                if expected_final_path and os.path.exists(expected_final_path):
                    logger.info(f"Video download successful: {expected_final_path}")
                    return expected_final_path
                    
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Video download failed: {e}")
    except Exception as e:
        logger.error(f"Error downloading video: {e}", exc_info=True)
    
    logger.error(f"Video download failed for {url}")
    return None


async def send_video_with_pyrogram(
    chat_id: int,
    file_path: str,
    ptb_bot_instance,
    processing_message_ptb,
    caption: str | None = None,
) -> bool:
    """Send a video file using Pyrogram, suitable for larger files."""
    if not API_ID or not API_HASH:
        logger.error("Pyrogram API_ID or API_HASH not configured.")
        return False

    pyrogram_session_name = "pyrogram_bot_session_video"
    
    progress_args_tuple = None
    if (
        processing_message_ptb
        and ptb_bot_instance
        and hasattr(processing_message_ptb, "chat_id")
        and hasattr(processing_message_ptb, "message_id")
    ):
        progress_args_tuple = (
            processing_message_ptb.chat_id,
            processing_message_ptb.message_id,
            ptb_bot_instance,
        )

    try:
        effective_api_id = int(API_ID)
        
        async with PyrogramClient(
            name=pyrogram_session_name,
            api_id=effective_api_id,
            api_hash=API_HASH,
            bot_token=TELEGRAM_BOT_TOKEN,
            in_memory=True,
        ) as app:
            logger.info(f"Pyrogram sending video: {file_path} to {chat_id}")
            await app.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=caption or "",
                supports_streaming=True,
                progress=pyrogram_upload_progress if progress_args_tuple else None,
                progress_args=progress_args_tuple if progress_args_tuple else (),
            )
            logger.info(f"Pyrogram successfully sent video to {chat_id}")
            if progress_args_tuple:
                progress_message_last_edit_time.pop(
                    (progress_args_tuple[0], progress_args_tuple[1]), None
                )
            return True
            
    except FloodWait as e_flood:
        logger.error(f"Pyrogram FloodWait: Must wait {e_flood.value} seconds.")
        return False
    except Exception as e:
        logger.error(f"Error sending video with Pyrogram: {e}", exc_info=True)
        return False


def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN not found in .env file. Please add it."
        )
        return
    if not API_ID or not API_HASH:  # Check for Pyrogram creds too
        logger.warning(
            "API_ID and/or API_HASH not found in .env. Sending large files "
            "via Pyrogram will fail."
        )
        # Allow bot to start, but Pyrogram part will be disabled.

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(60)  # Added for general connection establishment
        .read_timeout(60)  # General read timeout for API calls
        .write_timeout(60)  # General write timeout for API calls
        .pool_timeout(60)  # Timeout for connections in the pool
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    logger.info(
        "Starting bot with Pyrogram integration for large files & DEBUG "
        "logging..."
    )
    application.run_polling()
    logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
