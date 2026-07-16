import os
import threading
import logging
import tempfile
import subprocess

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

# ─── Auto-install javascript-obfuscator ────────────────
def _ensure_js_obfuscator():
    """Check & auto-install javascript-obfuscator npm package if missing."""
    try:
        r = subprocess.run(
            ["javascript-obfuscator", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            logger.info(f"✓ javascript-obfuscator ready: {r.stdout.strip()}")
            return
    except FileNotFoundError:
        pass

    logger.info("⚙️  javascript-obfuscator not found — installing via npm...")
    r = subprocess.run(
        ["npm", "install", "-g", "javascript-obfuscator"],
        capture_output=True, text=True, timeout=300
    )
    if r.returncode == 0:
        logger.info("✓ javascript-obfuscator installed successfully")
    else:
        logger.error(f"✗ npm install failed:\n{r.stderr[:400]}")

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
    "🛡️ <b>HTML Protector Bot</b>\n\n"
    "আপনার <code>.html</code> ফাইল পাঠান — <b>4 লেয়ারে</b> প্রোটেক্ট হবে:\n\n"
    "🔒 <b>Layer 1</b> — HTML+CSS Minify &amp; JS Obfuscation\n"
    "🔐 <b>Layer 2</b> — Body XOR Encryption + eval Self-Decode\n"
    "🚫 <b>Layer 3</b> — DevTools Detection (60ms loop)\n"
    "🌐 <b>Layer 4</b> — Full HTML→base64→single &lt;script&gt; tag\n"
    "┗ CSS সম্পূর্ণ hidden, শুধু একটা script tag থাকে\n\n"
    "📎 একটি <code>.html</code> ফাইল পাঠিয়ে শুরু করুন!"
)

DONE_CAPTION = (
    "✅ <b>4-Layer প্রোটেকশন সম্পন্ন!</b>\n\n"
    "🔒 Layer 1 — Minify + obfuscator.io ✓\n"
    "🔐 Layer 2 — XOR Encrypt + eval Decode ✓\n"
    "🚫 Layer 3 — DevTools 60ms Detection ✓\n"
    "🌐 Layer 4 — Single &lt;script&gt; tag only ✓\n\n"
    "<i>CSS, HTML structure সব hidden — শুধু একটা script tag দেখা যাবে</i>"
)

# ─── Handlers ──────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode="HTML")


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
                parse_mode="HTML",
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


# ─── Bot runner ────────────────────────────────────────────
# Uses run_polling() (high-level API) which:
#   ✓ Manages its own event loop → works on Python 3.11/3.12/3.13/3.14
#   ✓ Safe to call from a background thread (stop_signals=() disables
#     OS signal handlers which only work in the main thread)

def _bot_thread():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set — bot will not start")
        return
    try:
        bot_app = Application.builder().token(BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", cmd_start))
        bot_app.add_handler(CommandHandler("help",  cmd_help))
        bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
        logger.info("Telegram bot polling started ✓")
        bot_app.run_polling(
            drop_pending_updates=True,
            stop_signals=(),      # ← required for non-main thread
        )
    except Exception as e:
        logger.error(f"Bot thread crashed: {e}", exc_info=True)


# ─── Entry point ───────────────────────────────────────

if __name__ == "__main__":
    # ── Auto-install javascript-obfuscator ──
    _ensure_js_obfuscator()

    # ── Start bot in background thread ──
    t = threading.Thread(target=_bot_thread, daemon=True)
    t.start()
    logger.info("Bot thread started ✓")

    # Start Flask (main thread — Render binds to PORT)
    logger.info(f"Flask starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
