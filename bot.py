import os
import re
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from health_check import start_health_server

# ---------------- ENV ----------------

load_dotenv()

BOT_TOKEN = os.getenv("SHORTENER_BOT_TOKEN")
SHORTENER_DOMAIN = os.getenv("SHORTENER_DOMAIN")
MONGO_URI = os.getenv("MONGO_URI")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))  # e.g. -1001234567890

# ---------------- DATABASE ----------------

client = MongoClient(MONGO_URI)
db = client["shortener_bot"]
links_col = db["links"]
user_api_col = db["user_apis"]

# ---------------- DB FUNCTIONS ----------------

def get_user(user_id):
    return user_api_col.find_one({"userId": int(user_id)})


def get_user_api(user_id):
    user = get_user(user_id)
    return user["apiKey"] if user and "apiKey" in user else None


def save_user_api(user_id, api_key):
    user_api_col.update_one(
        {"userId": int(user_id)},
        {"$set": {"apiKey": api_key}},
        upsert=True
    )


def save_header(user_id, header):
    user_api_col.update_one(
        {"userId": int(user_id)},
        {"$set": {"header": header}},
        upsert=True
    )


def remove_header(user_id):
    user_api_col.update_one(
        {"userId": int(user_id)},
        {"$unset": {"header": ""}}
    )


def save_footer(user_id, footer):
    user_api_col.update_one(
        {"userId": int(user_id)},
        {"$set": {"footer": footer}},
        upsert=True
    )


def remove_footer(user_id):
    user_api_col.update_one(
        {"userId": int(user_id)},
        {"$unset": {"footer": ""}}
    )


def set_mode(user_id, mode):
    # mode: "keep_text" (default) or "delete_text"
    user_api_col.update_one(
        {"userId": int(user_id)},
        {"$set": {"mode": mode}},
        upsert=True
    )


def save_link(long_url, short_url, user_id):
    links_col.insert_one({
        "userId": int(user_id),
        "longURL": long_url,
        "shortURL": short_url,
        "created_at": datetime.now(timezone.utc)
    })


def increment_user_message_count(user_id):
    """Increment total messages processed for this user and return new count."""
    result = user_api_col.find_one_and_update(
        {"userId": int(user_id)},
        {"$inc": {"total_messages": 1}},
        upsert=True,
        return_document=True
    )
    return result.get("total_messages", 1)


# ---------------- UTILS ----------------

def extract_urls(text):
    pattern = r"https?://[^\s]+"
    return re.findall(pattern, text)


async def shorten_url(api_key, url):
    api_url = f"https://{SHORTENER_DOMAIN}/api"
    params = {"api": api_key, "url": url}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(api_url, params=params, timeout=15)
            data = r.json()
            if data.get("status") == "success":
                return data.get("shortenedUrl")
    except Exception:
        return None
    return None


async def build_reply(original_text, api_key, user_data, user_id):
    """
    Build reply caption based on user settings:
    - keep_text (default): replace URLs in original caption, add header/footer
    - delete_text: only shortened links + header/footer, no original text
    """
    header = user_data.get("header", "")
    footer = user_data.get("footer", "")
    mode = user_data.get("mode", "keep_text")

    urls = extract_urls(original_text or "")

    if not urls:
        return None

    # Shorten all URLs found in text
    url_map = {}
    for url in urls:
        short = await shorten_url(api_key, url)
        if short:
            url_map[url] = short
            save_link(url, short, user_id)

    if not url_map:
        return None

    parts = []

    if header:
        parts.append(header)

    if mode == "delete_text":
        # Only shortened links, original text removed
        parts.append("\n".join(url_map.values()))
    else:
        # keep_text (default): replace each URL in original caption
        result_text = original_text
        for original_url, short_url in url_map.items():
            result_text = result_text.replace(original_url, short_url)
        parts.append(result_text)

    if footer:
        parts.append(footer)

    return "\n".join(parts)


# ---------------- COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id) or {}
    api_key = user_data.get("apiKey")
    name = user.first_name or "User"

    if api_key:
        mode = user_data.get("mode", "keep_text")
        header = user_data.get("header", "—")
        footer = user_data.get("footer", "—")

        text = (
            f"👋 Welcome back, *{name}*!\n\n"
            f"✅ *API Key:* `{api_key}`\n"
            f"📌 *Mode:* `{mode}`\n"
            f"🔝 *Header:* {header}\n"
            f"🔚 *Footer:* {footer}\n\n"
            "Send any link or media with caption to shorten it."
        )
    else:
        text = (
            f"👋 Welcome, *{name}*!\n\n"
            "To get started, please set your API key:\n"
            "`/set_api YOUR_API_KEY`"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


async def set_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("Usage:\n`/set_api YOUR_API_KEY`", parse_mode="Markdown")
        return

    api_key = context.args[0]
    save_user_api(user_id, api_key)
    await update.message.reply_text("✅ API key saved successfully.")


async def add_header(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "Usage:\n`/add_header Your Header Text`\n\n"
            "Example:\n`/add_header Full Video Link`",
            parse_mode="Markdown"
        )
        return

    header = " ".join(context.args)
    save_header(user_id, header)
    await update.message.reply_text(f"✅ Header saved:\n{header}")


async def delete_header(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    remove_header(user_id)
    await update.message.reply_text("✅ Header deleted.")


async def add_footer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "Usage:\n`/add_footer Your Footer Text`\n\n"
            "Example:\n`/add_footer Join For More`",
            parse_mode="Markdown"
        )
        return

    footer = " ".join(context.args)
    save_footer(user_id, footer)
    await update.message.reply_text(f"✅ Footer saved:\n{footer}")


async def delete_footer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    remove_footer(user_id)
    await update.message.reply_text("✅ Footer deleted.")


async def keep_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_mode(user_id, "keep_text")
    await update.message.reply_text(
        "✅ *Keep Text* mode enabled.\n\n"
        "Original caption will be kept as-is, only links will be shortened.",
        parse_mode="Markdown"
    )


async def delete_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_mode(user_id, "delete_text")
    await update.message.reply_text(
        "✅ *Delete Text* mode enabled.\n\n"
        "Original caption will be removed. Only shortened links will be sent "
        "(along with your header/footer if set).",
        parse_mode="Markdown"
    )


# ---------------- LOG CHANNEL ----------------

async def log_to_channel(context, message, tg_user):
    """
    Copy original message to log channel,
    then send a summary info message.
    """
    if not LOG_CHANNEL_ID:
        return

    try:
        # Copy original message to log channel and get the new message id
        copied = await context.bot.copy_message(
            chat_id=LOG_CHANNEL_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

        username = f"@{tg_user.username}" if tg_user.username else "—"

        log_text = (
            f"📨 <b>Received From ...</b>\n\n"
            f"👤 <b>User ID :</b> <code>{tg_user.id}</code>\n"
            f"🔖 <b>Username :</b> {username}"
        )

        # Reply to the copied message with info
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_text,
            parse_mode="HTML",
            reply_to_message_id=copied.message_id
        )

    except Exception as e:
        print(f"[LOG CHANNEL ERROR] {e}")


# ---------------- MESSAGE HANDLER ----------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id

    user_data = get_user(user_id) or {}
    api_key = user_data.get("apiKey")

    if not api_key:
        await message.reply_text(
            "⚠ Please set your API key first.\nUse /set_api YOUR_API_KEY"
        )
        return

    text = message.text or message.caption

    if not text:
        return

    reply = await build_reply(text, api_key, user_data, user_id)

    if not reply:
        return  # No URL found, silently ignore

    # Reply with same media type + new caption
    if message.photo:
        await message.reply_photo(
            photo=message.photo[-1].file_id,
            caption=reply
        )
    elif message.video:
        await message.reply_video(
            video=message.video.file_id,
            caption=reply
        )
    elif message.document:
        await message.reply_document(
            document=message.document.file_id,
            caption=reply
        )
    elif message.animation:
        await message.reply_animation(
            animation=message.animation.file_id,
            caption=reply
        )
    else:
        await message.reply_text(reply)

    # Log to channel (original message + user info)
    await log_to_channel(context, message, update.effective_user)


# ---------------- MAIN ----------------

def main():
    # Start health check server (for Koyeb)
    start_health_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_api", set_api))
    app.add_handler(CommandHandler("add_header", add_header))
    app.add_handler(CommandHandler("delete_header", delete_header))
    app.add_handler(CommandHandler("add_footer", add_footer))
    app.add_handler(CommandHandler("delete_footer", delete_footer))
    app.add_handler(CommandHandler("keep_text", keep_text))
    app.add_handler(CommandHandler("delete_text", delete_text))

    app.add_handler(
        MessageHandler(
            filters.TEXT
            | filters.PHOTO
            | filters.VIDEO
            | filters.Document.ALL
            | filters.ANIMATION,
            handle_message
        )
    )

    print("Shortener Bot Started...")
    app.run_polling()


if __name__ == "__main__":
    main()
