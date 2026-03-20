# T212 Trading Bot

A trading bot that connects to your Trading 212 Practice or Real account via their API.

## Deploy to Railway (Free)

### Step 1 — Upload to GitHub
1. Go to https://github.com and create a free account if you don't have one
2. Click **New repository** → name it `t212-bot` → click **Create**
3. Upload all these files to the repo (drag and drop the whole folder)

### Step 2 — Deploy on Railway
1. Go to https://railway.app and sign up with your GitHub account (free)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `t212-bot` repository
4. Railway will auto-detect Python and deploy it
5. Once deployed, click your project → **Settings** → **Networking** → **Generate Domain**
6. You'll get a URL like `https://t212-bot-production.up.railway.app`

### Step 3 — Add your API key (optional but recommended)
Instead of typing your API key in the UI every time, you can set it as an environment variable:
1. In Railway, click your service → **Variables**
2. Add: `T212_API_KEY` = your key
3. Add: `T212_ACCOUNT_TYPE` = `demo` (or `live`)

### Step 4 — Use the bot
1. Open your Railway URL on your phone
2. Go to ⚙ Settings → paste your API key → Test Connection
3. Go to 🔍 Watchlist → add symbols (AAPL, TSLA, etc.)
4. Go to 📈 Dashboard → hit Start Bot

## Getting your T212 API Key
1. Open Trading 212 app
2. Tap your profile picture
3. **Make sure you're on Practice Account** (switch at top)
4. Settings → API (Beta) → Generate
5. Copy the key

## Files
- `app.py` — Python Flask server (handles T212 API, runs bot logic)
- `static/index.html` — Mobile-friendly UI
- `requirements.txt` — Python dependencies
- `Procfile` — tells Railway how to start the app
- `railway.json` — Railway config

## Notes
- The bot checks prices every 60 seconds by default (T212 rate limits)
- It uses RSI + MACD + EMA crossover + Bollinger Bands to generate signals
- A BUY fires when bull score ≥ 3.0, SELL when bear score ≥ 3.0
- Stop loss and take profit are tracked and auto-close positions
