import os
import asyncio
import threading
import logging
import tempfile

from flask import Flask, jsonify
from telegram import Update
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, filters, ContextTypes,
)
from protector import HTMLProtector

# ─── Config ────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
PORT         = int(os.environ.get("PORT", 10000))
MAX_FILE_MB  = 5
MAX_BYTES    = MAX_FILE_MB * 1024 * 1024

# ─── Logging ───────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Flask (health / keep-alive for Render + cron-job) ─
app = Flask(__name__)

@app.route("/")
def root():
    return "🛡️ HTML Protector Bot is running!", 200

@app.route("/health")
def health():
    return jsonify(status="ok"), 200

# ─── Messages ──────────────────────────────────────────
WELCOME = (
    "🛡️ *HTML Protector Bot*\n\n"
    "আপনার `.html` ফাইল পাঠান — 3 লেয়ারে প্রোটেক্ট হবে:\n\n"
    "🔒 *Layer 1* — HTML+CSS Minify & JS Obfuscation\n"
    "🔐 *Layer 2* — Body XOR Encryption \\+ eval Self\\-Decode\n"
    "🚫 *Layer 3* — DevTools Detection \\(60ms loop\\)\n"
    "┣ F12 / Ctrl\\+Shift\\+I / Ctrl\\+U → blank page\n"
    "┣ Eruda & vConsole \\(mobile\\) → blocked\n"
    "┗ Kiwi Browser → blocked\n\n"
    "📎 একটি `.html` ফাইল পাঠিয়ে শুরু করুন\\!"
)

DONE_CAPTION = (
    "✅ *প্রোটেকশন সম্পন্ন\\!*\n\n"
    "🔒 Layer 1 — Minify \\+ Obfuscate ✓\n"
    "🔐 Layer 2 — XOR Encrypt \\+ eval Decode ✓\n"
    "🚫 Layer 3 — DevTools 60ms Detection ✓\n\n"
    "_কেউ DevTools খুললেই blank page দেখবে_"
)

# ─── Handlers ──────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode="MarkdownV2")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode="MarkdownV2")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc   = update.message.document
    fname = doc.file_name or "file"

    # ── Validation ──
    if not fname.lower().endswith(".html"):
        await update.message.reply_text(
            "❌ শুধু `.html` ফাইল পাঠান।"
        )
        return

    if doc.file_size and doc.file_size > MAX_BYTES:
        await update.message.reply_text(
            f"❌ ফাইল সাইজ {MAX_FILE_MB}MB এর বেশি হওয়া যাবে না।"
        )
        return

    msg = await update.message.reply_text("⏳ প্রোসেস হচ্ছে…")

    tmp_in = tmp_out = None
    try:
        # ── Download ──
        tg_file = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="wb"
        ) as f:
            await tg_file.download_to_drive(f.name)
            tmp_in = f.name

        with open(tmp_in, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()

        if len(html.strip()) < 20:
            await msg.edit_text("❌ ফাইলটি খালি বা অনেক ছোট!")
            return

        # ── Protect ──
        protected = HTMLProtector().protect(html)

        # ── Write output ──
        with tempfile.NamedTemporaryFile(
            suffix="_protected.html", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(protected)
            tmp_out = f.name

        # ── Send ──
        await msg.delete()
        with open(tmp_out, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"protected_{fname}",
                caption=DONE_CAPTION,
                parse_mode="MarkdownV2",
            )

    except Exception as exc:
        logger.exception("Error processing file")
        try:
            await msg.edit_text(f"❌ Error: {str(exc)[:300]}")
        except Exception:
            pass
    finally:
        for p in (tmp_in, tmp_out):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


# ─── Bot runner (separate asyncio loop in its own thread) ─

async def _run_bot():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set — bot will not start")
        return
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", cmd_start))
    bot_app.add_handler(CommandHandler("help",  cmd_help))
    bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started ✓")
    await asyncio.Event().wait()   # run forever


def _bot_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_bot())
    except Exception as e:
        logger.error(f"Bot thread crashed: {e}")


# ─── Entry point ───────────────────────────────────────

if __name__ == "__main__":
    # Start bot in a daemon thread
    t = threading.Thread(target=_bot_thread, daemon=True)
    t.start()
    logger.info("Bot thread started ✓")

    # Start Flask (main thread — Render binds to PORT)
    logger.info(f"Flask starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
