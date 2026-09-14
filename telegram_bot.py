import asyncio
import io
import logging
import os
import threading
import time
from typing import Optional

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from scanner_core import (
    format_telegram_report,
    run_holehe_found_only,
    run_us_found_only_async,
    validate_email_target,
    validate_username_target,
)

logger = logging.getLogger("telegram_bot")

# Conversation States
AWAIT_HOLEHE_EMAIL, AWAIT_US_EMAIL, AWAIT_US_USERNAME = range(1, 4)


def _get_webapp_markup(webapp_url: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    """Build inline keyboard markup with Mini App launch button if URL configured."""
    if not webapp_url:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text="🚀 Open OSINT Suite", web_app=WebAppInfo(url=webapp_url))]
    ])


def _get_reply_menu_keyboard(webapp_url: Optional[str] = None) -> ReplyKeyboardMarkup:
    """Build persistent reply keyboard at bottom of chat."""
    rows = []
    if webapp_url:
        rows.append([KeyboardButton(text="🚀 Open OSINT Suite", web_app=WebAppInfo(url=webapp_url))])
    rows.append([KeyboardButton(text="/holhe"), KeyboardButton(text="/us-email")])
    rows.append([KeyboardButton(text="/us-name"), KeyboardButton(text="/cancel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /app and /miniapp commands to launch the Mini App."""
    webapp_url = os.getenv("TELEGRAM_WEBAPP_URL", "").strip()
    if not webapp_url:
        await update.message.reply_text(
            "⚠️ <b>Mini App URL is not configured yet.</b>\n"
            "Please set <code>TELEGRAM_WEBAPP_URL</code> in your <code>.env</code> file.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    markup = _get_webapp_markup(webapp_url)
    await update.message.reply_text(
        "📱 <b>OSINT Suite Mini App</b>\n\n"
        "Tap the button below to open the interactive web interface:",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
    return ConversationHandler.END


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start and /help commands."""
    webapp_url = os.getenv("TELEGRAM_WEBAPP_URL", "").strip()

    # Ensure chat menu button is the hamburger commands menu
    if update.effective_chat:
        try:
            await context.bot.set_chat_menu_button(
                chat_id=update.effective_chat.id,
                menu_button=MenuButtonCommands(),
            )
        except Exception as e:
            logger.debug("Could not set chat menu button: %s", e)

    welcome_text = (
        "👋 <b>Welcome to OSINT Suite!</b>\n\n"
        "You can launch the full interactive web app via the buttons below, "
        "or run quick scans directly in this chat using slash commands:\n\n"
        "🔹 <code>/app</code> — 🚀 Open OSINT Suite Mini App\n"
        "🔹 <code>/holhe</code> — Check email across 120+ platforms with Holehe\n"
        "🔹 <code>/us-email</code> — Check email across platforms with User Scanner\n"
        "🔹 <code>/us-name</code> — Check username across platforms with User Scanner\n"
        "🔹 <code>/cancel</code> — Cancel any pending command\n\n"
        "💡 <i>Use the quick action buttons below or type a slash command to begin!</i>"
    )

    # Use reply keyboard so interactive menu buttons appear directly on user's screen
    reply_kbd = _get_reply_menu_keyboard(webapp_url)
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_kbd,
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel any active conversation."""
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END


def _extract_target_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Extract argument if provided inline with the command or message."""
    if context.args:
        return " ".join(context.args).strip()
    if update.message and update.message.text:
        parts = update.message.text.strip().split(maxsplit=1)
        if len(parts) > 1 and not parts[1].startswith("/"):
            return parts[1].strip()
    return None


async def cmd_holhe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /holhe."""
    target_arg = _extract_target_arg(update, context)
    if target_arg:
        return await _execute_holehe_flow(update, context, target_arg)

    await update.message.reply_text(
        "📧 <b>Holehe Email Lookup</b>\n\n"
        "Please enter the email address you want to scan:\n"
        "<i>(Send /cancel to abort)</i>",
        parse_mode=ParseMode.HTML,
    )
    return AWAIT_HOLEHE_EMAIL


async def step_holehe_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle target email received after /holhe prompt."""
    raw_email = update.message.text.strip()
    return await _execute_holehe_flow(update, context, raw_email)


async def _execute_holehe_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_email: str) -> int:
    normalized, err = validate_email_target(raw_email)
    if err or not normalized:
        await update.message.reply_text(
            f"❌ {err or 'Invalid email'}\n\nPlease enter a valid email address (or /cancel):",
        )
        return AWAIT_HOLEHE_EMAIL

    status_msg = await update.message.reply_text(
        f"🔎 <b>Scanning</b> <code>{normalized}</code> with Holehe...\n"
        f"⏳ Checking platforms, please wait...",
        parse_mode=ParseMode.HTML,
    )

    start_time = time.time()
    try:
        # Run holehe in thread pool to prevent blocking
        found_sites, scan_err = await asyncio.to_thread(run_holehe_found_only, normalized)
        elapsed = time.time() - start_time

        if scan_err and not found_sites:
            await status_msg.edit_text(
                f"⚠️ <b>Holehe scan error:</b>\n{scan_err}",
                parse_mode=ParseMode.HTML,
            )
            return ConversationHandler.END

        msg_text, overflow_txt = format_telegram_report(
            normalized, "Holehe (Found Only)", found_sites, elapsed_seconds=elapsed
        )

        webapp_url = os.getenv("TELEGRAM_WEBAPP_URL", "").strip()
        markup = _get_webapp_markup(webapp_url)

        await status_msg.edit_text(
            msg_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=markup,
        )

        if overflow_txt:
            bio = io.BytesIO(overflow_txt.encode("utf-8"))
            bio.name = f"holehe_{normalized.replace('@', '_at_')}.txt"
            await update.message.reply_document(
                document=bio,
                caption=f"📄 Full found accounts list for {normalized}",
            )

    except Exception as e:
        logger.exception("Error executing Holehe scan: %s", e)
        await status_msg.edit_text(f"❌ An error occurred during scan: {e}")

    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# User-Scanner Email Command & Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_us_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /us-email."""
    target_arg = _extract_target_arg(update, context)
    if target_arg:
        return await _execute_us_email_flow(update, context, target_arg)

    await update.message.reply_text(
        "📧 <b>User Scanner — Email Lookup</b>\n\n"
        "Please enter the email address you want to scan:\n"
        "<i>(Send /cancel to abort)</i>",
        parse_mode=ParseMode.HTML,
    )
    return AWAIT_US_EMAIL


async def step_us_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle target email received after /us-email prompt."""
    raw_email = update.message.text.strip()
    return await _execute_us_email_flow(update, context, raw_email)


async def _execute_us_email_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_email: str) -> int:
    normalized, err = validate_email_target(raw_email)
    if err or not normalized:
        await update.message.reply_text(
            f"❌ {err or 'Invalid email'}\n\nPlease enter a valid email address (or /cancel):",
        )
        return AWAIT_US_EMAIL

    return await _run_us_scan(update, normalized, is_email=True)


# ─────────────────────────────────────────────────────────────────────────────
# User-Scanner Username Command & Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_us_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /us-name."""
    target_arg = _extract_target_arg(update, context)
    if target_arg:
        return await _execute_us_username_flow(update, context, target_arg)

    await update.message.reply_text(
        "👤 <b>User Scanner — Username Lookup</b>\n\n"
        "Please enter the username you want to scan:\n"
        "<i>(Send /cancel to abort)</i>",
        parse_mode=ParseMode.HTML,
    )
    return AWAIT_US_USERNAME


async def step_us_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle target username received after /us-name prompt."""
    raw_username = update.message.text.strip()
    return await _execute_us_username_flow(update, context, raw_username)


async def _execute_us_username_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_username: str) -> int:
    cleaned, err = validate_username_target(raw_username)
    if err or not cleaned:
        await update.message.reply_text(
            f"❌ {err or 'Invalid username'}\n\nPlease enter a valid username (or /cancel):",
        )
        return AWAIT_US_USERNAME

    return await _run_us_scan(update, cleaned, is_email=False)


# ─────────────────────────────────────────────────────────────────────────────
# Common User-Scanner Runner with Live Progress Edits
# ─────────────────────────────────────────────────────────────────────────────

async def _run_us_scan(update: Update, target: str, is_email: bool) -> int:
    mode_label = "Email" if is_email else "Username"
    status_msg = await update.message.reply_text(
        f"🔎 <b>Scanning {mode_label}</b> <code>{target}</code> with User Scanner...\n"
        f"⏳ Preparing modules...",
        parse_mode=ParseMode.HTML,
    )

    start_time = time.time()
    last_edit_time = 0.0

    async def on_progress(done: int, total: int, found_count: int):
        nonlocal last_edit_time
        now = time.time()
        # Keep edit rate conservative (>= 2 seconds) to avoid Telegram rate limits
        if (now - last_edit_time < 2.0) and done < total:
            return
        last_edit_time = now

        pct = int((done / total) * 100) if total else 0
        filled = int(pct / 10)
        bar = "■" * filled + "□" * (10 - filled)
        try:
            await status_msg.edit_text(
                f"🔎 <b>Scanning {mode_label}</b> <code>{target}</code> with User Scanner\n"
                f"[{bar}] {pct}% ({done}/{total})\n"
                f"✅ Found so far: <b>{found_count}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    try:
        found_items, err = await run_us_found_only_async(
            target=target,
            is_email=is_email,
            allow_loud=False,
            progress_callback=on_progress,
        )
        elapsed = time.time() - start_time

        if err and not found_items:
            await status_msg.edit_text(
                f"⚠️ <b>Scan error:</b>\n{err}",
                parse_mode=ParseMode.HTML,
            )
            return ConversationHandler.END

        engine_name = f"User Scanner ({mode_label})"
        msg_text, overflow_txt = format_telegram_report(
            target, engine_name, found_items, elapsed_seconds=elapsed
        )

        webapp_url = os.getenv("TELEGRAM_WEBAPP_URL", "").strip()
        markup = _get_webapp_markup(webapp_url)

        await status_msg.edit_text(
            msg_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=markup,
        )

        if overflow_txt:
            bio = io.BytesIO(overflow_txt.encode("utf-8"))
            bio.name = f"userscanner_{target.replace('@', '_at_')}.txt"
            await update.message.reply_document(
                document=bio,
                caption=f"📄 Full found accounts list for {target}",
            )

    except Exception as e:
        logger.exception("Error during User Scanner run: %s", e)
        await status_msg.edit_text(f"❌ An error occurred during scan: {e}")

    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Bot Setup & Embedded Background Thread Launcher
# ─────────────────────────────────────────────────────────────────────────────

async def post_init_setup(app: Application) -> None:
    """Register bot commands and ensure hamburger menu button is active."""
    bot_commands = [
        BotCommand("app", "🚀 Open OSINT Suite Mini App"),
        BotCommand("holhe", "📧 Scan email with Holehe"),
        BotCommand("us_email", "📧 Scan email with User Scanner"),
        BotCommand("us_name", "👤 Scan username with User Scanner"),
        BotCommand("start", "👋 Welcome & Instructions"),
        BotCommand("cancel", "❌ Cancel current action"),
    ]
    try:
        await app.bot.set_my_commands(bot_commands)
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Bot commands and hamburger menu registered successfully.")
    except Exception as e:
        logger.warning("Could not register bot commands: %s", e)


def create_bot_application(bot_token: str) -> Application:
    """Build the Telegram Application with handlers."""
    application = (
        ApplicationBuilder()
        .token(bot_token)
        .post_init(post_init_setup)
        .build()
    )

    # Conversation handler for interactive commands
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler(["holhe", "holehe"], cmd_holhe),
            CommandHandler(["usemail", "us_email"], cmd_us_email),
            CommandHandler(["usname", "us_name"], cmd_us_name),
            MessageHandler(filters.Regex(r"^/(us-email|us_email|usemail)(\s+.*)?$"), cmd_us_email),
            MessageHandler(filters.Regex(r"^/(us-name|us_name|usname)(\s+.*)?$"), cmd_us_name),
            MessageHandler(filters.Regex(r"^/(holhe|holehe)(\s+.*)?$"), cmd_holhe),
        ],
        states={
            AWAIT_HOLEHE_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_holehe_email),
            ],
            AWAIT_US_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_us_email),
            ],
            AWAIT_US_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, step_us_username),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler(["app", "miniapp"], cmd_app),
            CommandHandler("start", cmd_start),
            CommandHandler("help", cmd_start),
        ],
    )

    application.add_handler(CommandHandler(["app", "miniapp"], cmd_app))
    application.add_handler(CommandHandler(["start", "help"], cmd_start))
    application.add_handler(conv_handler)
    return application


_bot_thread: Optional[threading.Thread] = None


def start_embedded_bot(bot_token: str, webapp_url: Optional[str] = None):
    """Start the Telegram bot in a background daemon thread."""
    global _bot_thread
    if _bot_thread is not None and _bot_thread.is_alive():
        logger.info("Telegram bot thread already running.")
        return

    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            logger.info("Starting Telegram bot polling thread...")
            app = create_bot_application(bot_token)
            # Run polling inside thread's event loop
            loop.run_until_complete(app.initialize())
            loop.run_until_complete(post_init_setup(app))
            loop.run_until_complete(app.start())
            loop.run_until_complete(app.updater.start_polling(allowed_updates=Update.ALL_TYPES))
            loop.run_forever()
        except Exception as e:
            logger.error("Telegram bot runner stopped with error: %s", e)
        finally:
            try:
                loop.run_until_complete(app.stop())
                loop.run_until_complete(app.shutdown())
            except Exception:
                pass
            loop.close()

    _bot_thread = threading.Thread(target=_runner, name="TelegramBotWorker", daemon=True)
    _bot_thread.start()
    logger.info("Embedded Telegram bot worker launched.")
