# 🛡️ HTML Protector Bot

Telegram bot — `.html` ফাইল পাঠালে 3 লেয়ারে প্রোটেক্ট করে ফেরত দেয়।

---

## ⚙️ Protection Layers

| Layer | কী করে |
|-------|---------|
| 🔒 Layer 1 | HTML + CSS minify, inline JS obfuscate (eval+atob+chunk) |
| 🔐 Layer 2 | Body content XOR encrypt → base64 → eval দিয়ে browser memory-তে decode |
| 🚫 Layer 3 | DevTools 60ms detection loop, keyboard block, Eruda/vConsole/Kiwi block |

---

## 🚀 Deploy — Render

### Step 1 — BotFather থেকে Token নিন

1. Telegram-এ `@BotFather` খুলুন
2. `/newbot` → নাম ও username দিন
3. Token কপি করুন → `7123456789:AAFxxx...`

### Step 2 — Render-এ Deploy করুন

1. **[render.com](https://render.com)** → Sign up / Log in
2. **New → Web Service**
3. **GitHub repo connect করুন** (এই ফাইলগুলো push করুন)
   - অথবা "Upload" করুন manually
4. Settings:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Plan:** Free

### Step 3 — Environment Variable সেট করুন

Render Dashboard → আপনার Service → **Environment**:

```
BOT_TOKEN = 7123456789:AAFxxxxxxxxxxxxxxxxxxx
```

**Save → Deploy হবে।**

আপনার bot URL হবে:
```
https://your-app-name.onrender.com
```

---

## ⏰ cron-job.org — Keep Alive (বাধ্যতামূলক!)

Render Free-tier 15 মিনিট idle থাকলে ঘুমিয়ে পড়ে।  
cron-job.org দিয়ে প্রতি 5 মিনিটে ping করুন:

1. **[cron-job.org](https://cron-job.org)** → Sign up
2. **Create Cronjob:**
   - **URL:** `https://your-app-name.onrender.com/health`
   - **Schedule:** Every 5 minutes
   - **Method:** GET
3. **Save**

এখন bot সারাদিন জেগে থাকবে।

---

## 📁 File Structure

```
html-protector-bot/
├── bot.py           # Telegram bot + Flask health server
├── protector.py     # 3-layer HTML protection engine
├── requirements.txt
├── Procfile
├── render.yaml
└── README.md
```

---

## 💬 Bot ব্যবহার

```
/start  — স্বাগতম বার্তা
/help   — সাহায্য

[.html ফাইল পাঠান] → protected_filename.html ফেরত পাবেন
```

---

## 🔬 Technical Details

### Layer 2 — Encryption Flow
```
Original body HTML
      ↓  XOR with random 32-char key
   Encrypted bytes
      ↓  base64 encode
   Safe ASCII blob (embedded in <script>)
      ↓  Browser runs decoder
   eval('document.body.innerHTML=' + JSON.stringify(decoded))
      ↓
   Page renders normally ✓
   DevTools Elements → shows only encrypted blob ✓
```

### Layer 3 — Detection Methods
```
① Window size: outerWidth - innerWidth > 160px (100px for Kiwi)
② Console getter: Object.defineProperty id getter triggered by console.log
③ Eruda/vConsole DOM element check (mobile devtools)
④ Debugger timing: performance.now() diff > 100ms
⑤ Keyboard: F12, Ctrl+Shift+I/J/C, Ctrl+U blocked
⑥ Print dialog blocked
⑦ Anti-iframe (view-source trick blocked)
⑧ BroadcastChannel: same-origin tabs সব blank হয়
```

---

## ⚠️ Limitations

- 100% protection সম্ভব না — determined expert ব্যপাস করতে পারে
- JS disabled browser-এ page কাজ করবে না
- Max file size: 5MB
