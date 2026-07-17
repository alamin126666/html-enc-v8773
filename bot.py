import os
import threading
import logging
import tempfile
import subprocess

import httpx
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler,
)
from protector import HTMLProtector

# ─── Config ────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
PORT        = int(os.environ.get("PORT", 10000))
MAX_FILE_MB = 5
MAX_BYTES   = MAX_FILE_MB * 1024 * 1024

# ─── User states ───────────────────────────────────────
ENC_MODE   = "enc"    # waiting for .html file
FETCH_MODE = "fetch"  # waiting for URL
# None = idle (buttons not pressed yet → ignore all input)

# ─── Logging ───────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── AES Challenge Solver ──────────────────────────────
def _solve_aes_challenge(html: str):
    """
    Detect & solve slowAES.decrypt() cookie challenge.
    var a=KEY, b=IV, c=CIPHERTEXT → AES-CBC decrypt → __test cookie
    Returns (cookie_value, redirect_url) or None if not a challenge page.
    """
    if "slowAES.decrypt" not in html:
        return None
    import re
    try:
        a    = re.search(r'var a=toNumbers\("([0-9a-f]+)"\)', html).group(1)
        b    = re.search(r'var b=toNumbers\("([0-9a-f]+)"\)', html).group(1)
        c    = re.search(r'var c=toNumbers\("([0-9a-f]+)"\)', html).group(1)
        href = re.search(r'location\.href=["\']([^"\']+)["\']', html).group(1)
    except AttributeError:
        return None
    try:
        from Crypto.Cipher import AES as _AES
        key      = bytes.fromhex(a)
        iv_bytes = bytes.fromhex(b)
        ct       = bytes.fromhex(c)
        decrypted = _AES.new(key, _AES.MODE_CBC, iv_bytes).decrypt(ct)
        logger.info(f"AES challenge solved → cookie: {decrypted.hex()[:16]}...")
        return decrypted.hex(), href
    except Exception as e:
        logger.warning(f"AES solve failed: {e}")
        return None
flask_app = Flask(__name__)

@flask_app.route("/")
def root():
    return "🛡️ HTML Protector Bot is running!", 200

@flask_app.route("/health")
def health():
    return jsonify(status="ok"), 200

# ─── Auto-install javascript-obfuscator ────────────────
def _ensure_js_obfuscator():
    try:
        r = subprocess.run(
            ["javascript-obfuscator", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            logger.info(f"✓ javascript-obfuscator: {r.stdout.strip()}")
            return
    except FileNotFoundError:
        pass
    logger.info("⚙️  Installing javascript-obfuscator via npm...")
    r = subprocess.run(
        ["npm", "install", "-g", "javascript-obfuscator"],
        capture_output=True, text=True, timeout=300
    )
    if r.returncode == 0:
        logger.info("✓ javascript-obfuscator installed")
    else:
        logger.error(f"✗ npm install failed: {r.stderr[:400]}")

# ─── UI helpers ────────────────────────────────────────
def _main_kb():
    """Main inline keyboard — shown on /start and after each action."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔒 HTML ENC",  callback_data="btn_enc"),
        InlineKeyboardButton("🌐 HTML FECH", callback_data="btn_fetch"),
    ]])

WELCOME = (
    "🛡️ <b>HTML Protector Bot</b>\n\n"
    "নিচের বাটন থেকে কাজ বেছে নিন:\n\n"
    "🔒 <b>HTML ENC</b> — HTML ফাইল 4-layer encrypt করুন\n"
    "🌐 <b>HTML FECH</b> — URL থেকে HTML নামিয়ে নিন"
)

# ─── /start & /help ────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()          # reset any previous state
    await update.message.reply_text(
        WELCOME, parse_mode="HTML", reply_markup=_main_kb()
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

# ─── Button handler ────────────────────────────────────
async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "btn_enc":
        context.user_data["state"] = ENC_MODE
        await query.edit_message_text(
            "🔒 <b>HTML Encryption Mode</b>\n\n"
            "আপনার <code>.html</code> ফাইল পাঠান:",
            parse_mode="HTML",
        )

    elif query.data == "btn_fetch":
        context.user_data["state"] = FETCH_MODE
        await query.edit_message_text(
            "🌐 <b>HTML Fetch Mode</b>\n\n"
            "Website URL পাঠান:\n"
            "<code>https://example.com</code>",
            parse_mode="HTML",
        )

# ─── HTML ENC handler ──────────────────────────────────
async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept .html file ONLY when user clicked [ HTML ENC ]"""
    if context.user_data.get("state") != ENC_MODE:
        return   # button not pressed → silent ignore

    doc   = update.message.document
    fname = doc.file_name or "file.html"

    if not fname.lower().endswith(".html"):
        await update.message.reply_text(
            "❌ শুধু <code>.html</code> ফাইল পাঠান।",
            parse_mode="HTML"
        )
        return

    if doc.file_size and doc.file_size > MAX_BYTES:
        await update.message.reply_text(
            f"❌ ফাইল {MAX_FILE_MB}MB এর বেশি হওয়া যাবে না।"
        )
        return

    msg = await update.message.reply_text("⏳ Encrypting...")
    tmp_in = tmp_out = None

    try:
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

        protected = HTMLProtector().protect(html)

        with tempfile.NamedTemporaryFile(
            suffix="_protected.html", delete=False,
            mode="w", encoding="utf-8"
        ) as f:
            f.write(protected)
            tmp_out = f.name

        await msg.delete()
        with open(tmp_out, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{fname.rsplit(".", 1)[0]}_obf.html",
                caption=(
                    "✅ <b>4-Layer Protection সম্পন্ন!</b>\n\n"
                    "🔒 Layer 1 — Minify + RC4 Obfuscate ✓\n"
                    "🔐 Layer 2 — Double XOR Encrypt ✓\n"
                    "🚫 Layer 3 — DevTools Detection ✓\n"
                    "🌐 Layer 4 — Single &lt;script&gt; tag ✓"
                ),
                parse_mode="HTML",
            )

        # reset state → show buttons again
        context.user_data.clear()
        await update.message.reply_text(
            "আরো কিছু করতে চান?", reply_markup=_main_kb()
        )

    except Exception as exc:
        logger.exception("Encryption error")
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

# ─── HTML FECH handler ─────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept URL ONLY when user clicked [ HTML FECH ]"""
    if context.user_data.get("state") != FETCH_MODE:
        return   # button not pressed → silent ignore

    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    msg = await update.message.reply_text("⏳ Fetching...")
    tmp_path = None

    try:
        _hdrs = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # ── Step 1: Initial fetch ───────────────────────────
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=_hdrs
        ) as client:
            resp = await client.get(url)

        html = resp.text

        # ── Step 2: AES Challenge Detection & Solve ─────────
        challenge = _solve_aes_challenge(html)
        if challenge:
            cookie_val, redirect_url = challenge
            await msg.edit_text(
                "🔓 AES challenge detected — cookie solve করছি..."
            )
            logger.info(f"AES solved → retrying {redirect_url}")

            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers=_hdrs,
                cookies={"__test": cookie_val}
            ) as client:
                resp = await client.get(redirect_url)

            html = resp.text
            url  = redirect_url   # update for caption

        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        fname  = f"{domain}.html"

        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(html)
            tmp_path = f.name

        await msg.delete()
        with open(tmp_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=fname,
                caption=(
                    f"✅ <b>HTML Fetched!</b>\n\n"
                    f"🌐 <code>{url[:80]}</code>\n"
                    f"📊 Size: {len(html):,} bytes\n"
                    f"📡 Status: {resp.status_code}"
                ),
                parse_mode="HTML",
            )

        # reset state → show buttons again
        context.user_data.clear()
        await update.message.reply_text(
            "আরো কিছু করতে চান?", reply_markup=_main_kb()
        )

    except httpx.TimeoutException:
        await msg.edit_text("❌ Timeout — URL টি সময়মতো respond করেনি।")
    except httpx.InvalidURL:
        await msg.edit_text("❌ Invalid URL — সঠিক URL দিন।")
    except Exception as exc:
        logger.exception("Fetch error")
        try:
            await msg.edit_text(f"❌ Error: {str(exc)[:300]}")
        except Exception:
            pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

# ─── Bot runner ────────────────────────────────────────
def _bot_thread():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set — bot will not start")
        return
    try:
        bot_app = Application.builder().token(BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", cmd_start))
        bot_app.add_handler(CommandHandler("help",  cmd_help))
        bot_app.add_handler(CallbackQueryHandler(btn_handler))
        bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
        bot_app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
        )
        logger.info("✓ Bot polling started")
        bot_app.run_polling(drop_pending_updates=True, stop_signals=())
    except Exception as exc:
        logger.error(f"Bot thread error: {exc}", exc_info=True)

# ─── Entry point ───────────────────────────────────────
if __name__ == "__main__":
    _ensure_js_obfuscator()

    t = threading.Thread(target=_bot_thread, daemon=True)
    t.start()
    logger.info(f"Flask starting on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True)
