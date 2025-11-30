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
        "Hi! I convert YouTube videos to audio files.\n\n"
        "Simply paste a YouTube video link directly into the chat and "
        "send it to me.",
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

        processing_message = None
        # Try to send initial "Processing" message, but don't fail if it
        # errors
        try:
            processing_message = await update.message.reply_text(
                "Processing your request, please wait..."
            )
        except Exception as e_proc_msg:
            logger.warning(
                "Could not send initial 'Processing...' message: "
                f"{e_proc_msg}"
            )

        audio_sent_successfully = False
        # Initialize to ensure it's always defined for logging/cleanup
        audio_file_path = None
        file_size = 0  # Initialize file_size

        try:
            audio_file_path = await download_and_convert_youtube(
                youtube_url, video_id
            )
            if audio_file_path:
                file_size = os.path.getsize(audio_file_path)
                logger.info(
                    f"Audio file created: {audio_file_path}, Size: "
                    f"{file_size} bytes"
                )
                TELEGRAM_AUDIO_LIMIT_BYTES = 50 * 1024 * 1024

                if file_size > TELEGRAM_AUDIO_LIMIT_BYTES:
                    logger.info(
                        f"File size {file_size // (1024*1024)}MB > PTB limit. "
                        "Attempting send with Pyrogram."
                    )
                    initial_pyro_message = (
                        "Audio is large, preparing to send with an "
                        "alternative method..."
                    )
                    if processing_message:
                        try:
                            await processing_message.edit_text(
                                initial_pyro_message
                            )
                        except Exception as e_edit_proc:
                            logger.warning(
                                "Could not edit 'Processing...' for "
                                f"Pyrogram: {e_edit_proc}"
                            )
                    # If the original processing_message failed, send a new
                    # one
                    elif update.message:
                        try:
                            processing_message = (
                                await update.message.reply_text(
                                    initial_pyro_message
                                )
                            )
                        except Exception as e_new_proc:
                            logger.warning(
                                "Could not send new 'Processing...' message "
                                f"for Pyrogram: {e_new_proc}"
                            )

                    pyrogram_sent = await send_audio_with_pyrogram(
                        chat_id,
                        audio_file_path,
                        context.bot,
                        processing_message,
                        caption=f"Audio from: {youtube_url}",
                    )

                    if pyrogram_sent:
                        audio_sent_successfully = True
                        logger.info(
                            "Pyrogram successfully sent large audio: "
                            f"{audio_file_path}"
                        )
                        # Attempt to delete the progress message if it exists
                        if processing_message:
                            try:
                                await processing_message.delete()
                                logger.info(
                                    "Deleted Pyrogram progress message."
                                )
                                # Clear message to prevent double cleanup
                                processing_message = None
                            except Exception as e_del_pyro_prog:
                                logger.warning(
                                    "Could not delete Pyrogram progress "
                                    f"message: {e_del_pyro_prog}"
                                )
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="Large audio sent! What next?",
                            reply_markup=get_info_inline_keyboard(),
                        )
                    else:
                        logger.error(
                            "Pyrogram failed to send large audio: "
                            f"{audio_file_path}"
                        )
                        error_message = (
                            "Failed to send the large audio file. It might "
                            "be too large or a temporary issue occurred."
                        )
                        if processing_message:
                            try:
                                await processing_message.edit_text(
                                    error_message
                                )
                            except Exception as e_edit_fail:
                                logger.warning(
                                    "Could not edit 'Processing...' on "
                                    f"Pyrogram fail: {e_edit_fail}"
                                )
                        elif update.message:
                            await update.message.reply_text(
                                error_message,
                                reply_markup=get_info_inline_keyboard(),
                            )

                else:
                    logger.info(
                        f"Sending audio via python-telegram-bot: "
                        f"{audio_file_path} (size "
                        f"{file_size // (1024*1024)}MB) to {chat_id}"
                    )
                    try:
                        # Renamed to avoid conflict
                        with open(audio_file_path, "rb") as audio_file_obj:
                            await context.bot.send_audio(
                                chat_id=chat_id,
                                audio=audio_file_obj,
                                read_timeout=180,
                                write_timeout=180,
                                connect_timeout=180,
                            )
                        audio_sent_successfully = True
                        logger.info(
                            f"Sent audio {audio_file_path} to {chat_id} "
                            "via PTB"
                        )
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="Audio sent! What next?",
                            reply_markup=get_info_inline_keyboard(),
                        )
                    except telegram.error.TimedOut as e_timeout:
                        logger.warning(
                            f"Timeout sending audio {audio_file_path} "
                            f"(PTB): {e_timeout}. User might have it."
                        )
                        audio_sent_successfully = True
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="Audio sent (timeout confirming). What next?",
                            reply_markup=get_info_inline_keyboard(),
                        )
                    except httpx.ReadError as e_read:
                        logger.error(
                            f"httpx.ReadError sending audio "
                            f"{audio_file_path} (PTB): {e_read}",
                            exc_info=True,
                        )
                        if processing_message:
                            try:
                                await processing_message.edit_text(
                                    "A network read error occurred while "
                                    "sending (PTB). Please try again.\n"
                                    f"Details: {e_read}"
                                )
                            except Exception as e_edit_read:
                                logger.warning(
                                    "Could not edit 'Processing...' on "
                                    f"PTB ReadError: {e_edit_read}"
                                )
                        elif update.message:
                            await update.message.reply_text(
                                "A network read error occurred (PTB). "
                                "Please try again.\n"
                                f"Details: {e_read}",
                                reply_markup=get_info_inline_keyboard(),
                            )
                    except Exception as e_send:
                        logger.error(
                            f"Failed to send audio {audio_file_path} (PTB): "
                            f"{e_send}",
                            exc_info=True,
                        )
                        if processing_message:
                            try:
                                await processing_message.edit_text(
                                    f"Error sending audio (PTB): {e_send}"
                                )
                            except Exception as e_edit_send:
                                logger.warning(
                                    "Could not edit 'Processing...' on PTB "
                                    f"send error: {e_edit_send}"
                                )
                        elif update.message:
                            await update.message.reply_text(
                                f"Error sending audio (PTB): {e_send}",
                                reply_markup=get_info_inline_keyboard(),
                            )

                if audio_sent_successfully and processing_message:
                    try:
                        await processing_message.delete()
                        logger.info("Deleted 'Processing...' msg.")
                    except Exception as e_del:
                        logger.warning(
                            "Could not delete 'Processing...' msg: "
                            f"{e_del} (it might have been edited or "
                            "already deleted)"
                        )

            else:
                logger.warning(
                    "download_and_convert_youtube returned None for "
                    f"{youtube_url}."
                )
                if processing_message:
                    try:
                        await processing_message.edit_text(
                            "Couldn't process YouTube link. Check logs."
                        )
                    except Exception as e_edit_dlfail:
                        logger.warning(
                            "Could not edit 'Processing...' on DL fail: "
                            f"{e_edit_dlfail}"
                        )
                elif update.message:
                    await update.message.reply_text(
                        "Couldn't process YouTube link. Please check the "
                        "link or try again later.",
                        reply_markup=get_info_inline_keyboard(),
                    )

        except Exception as e:
            logger.error(
                f"Generic error in handle_message for {youtube_url}: {e}",
                exc_info=True,
            )
            if not audio_sent_successfully and processing_message:
                try:
                    await processing_message.edit_text(
                        "An unexpected error occurred: {e}. Please check "
                        "logs or try again."
                    )
                except Exception as e_edit_generic:
                    logger.error(
                        "Failed to edit 'Processing...' on generic error: "
                        f"{e_edit_generic}"
                    )
            # If initial processing_message might have failed or doesn't
            # exist
            elif update.message:
                await update.message.reply_text(
                    f"An unexpected error occurred: {e}. Please try again "
                    "later.",
                    reply_markup=get_info_inline_keyboard(),
                )

        finally:  # Ensure cleanup happens
            if audio_file_path and os.path.exists(audio_file_path):
                try:
                    os.remove(audio_file_path)
                    logger.info(
                        f"Removed temp file: {audio_file_path} in finally "
                        "block."
                    )
                except OSError as e_rem_finally:
                    logger.error(
                        f"Error removing temp {audio_file_path} in finally "
                        f"block: {e_rem_finally}"
                    )
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


async def download_and_convert_youtube(url: str, video_id: str) -> str | None:
    """
    Download a YouTube video and convert it to an MP3 file.

    This function uses yt-dlp to download a video from a given URL and
    FFmpeg to convert it into an MP3 audio file. It tries multiple
    strategies to ensure the download is successful.
    """
    # Output template for yt-dlp before FFmpeg processing.
    # FFmpeg will add the .mp3
    base_output_template = f"{video_id}"
    # The final path we expect after FFmpeg has converted it to mp3
    expected_final_path = f"{base_output_template}.mp3"

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
                ydl.download([url])

            logger.info(
                f"yt-dlp download & FFmpeg conversion for {url} completed "
                f"using strategy '{strategy['name']}'."
            )

            if os.path.exists(expected_final_path):
                logger.info(
                    "Conversion successful. MP3 file created: "
                    f"{expected_final_path}"
                )
                return expected_final_path
            else:
                logger.error(
                    "CRITICAL: Expected MP3 file "
                    f"{expected_final_path} was NOT found after FFmpeg "
                    f"processing for {url}."
                )
                # Check if the pre-ffmpeg file exists
                # (e.g., video_id.webm or video_id.m4a)
                # This might give a clue if FFmpeg failed or was skipped.
                downloaded_files_before_ffmpeg = [
                    f
                    for f in os.listdir(".")
                    if f.startswith(video_id) and not f.endswith(".mp3")
                ]
                if downloaded_files_before_ffmpeg:
                    logger.error(
                        "Found these files that might be pre-FFmpeg output: "
                        f"{downloaded_files_before_ffmpeg}"
                    )
                else:
                    logger.error(
                        "No intermediate files (like "
                        f"{video_id}.webm/m4a) found either."
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
            if os.path.exists(expected_final_path):  # Clean up
                os.remove(expected_final_path)
            # Also clean up any intermediate files yt-dlp might have
            # left if FFmpeg errored
            intermediate_files = [
                f
                for f in os.listdir(".")
                if f.startswith(base_output_template)
                and f != expected_final_path
            ]
            for int_file in intermediate_files:
                try:
                    os.remove(int_file)
                    logger.info(f"Cleaned up intermediate file: {int_file}")
                except OSError as e_rem_int:
                    logger.error(
                        f"Error removing intermediate file {int_file}: "
                        f"{e_rem_int}"
                    )
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
            if os.path.exists(expected_final_path):
                os.remove(expected_final_path)
            intermediate_files = [
                f
                for f in os.listdir(".")
                if f.startswith(base_output_template)
                and f != expected_final_path
            ]
            for int_file in intermediate_files:
                try:
                    os.remove(int_file)
                    logger.info(
                        f"Cleaned up intermediate file: {int_file} on "
                        "general error."
                    )
                except OSError as e_rem_int_gen:
                    logger.error(
                        f"Error removing intermediate file {int_file} on "
                        f"general error: {e_rem_int_gen}"
                    )
            return None

    # If we get here, all strategies failed
    logger.error(f"All download strategies failed for {url}")
    return None


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
