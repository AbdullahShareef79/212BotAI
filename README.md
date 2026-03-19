# 🤖 StockBot AI – AI-Powered Trading Bot for Trading 212

An automated stock trading bot that combines **OpenAI sentiment analysis** with **technical indicators** (RSI, MACD, Bollinger Bands) to execute trades via the **Trading 212 API**.

---

## ✨ Features

| Feature | Description |
|---|---|
| 📊 **Technical Analysis** | RSI(14), MACD(12,26,9), Bollinger Bands(20,2) on daily candles |
| 🧠 **AI Sentiment** | GPT-4o-mini analyzes recent news headlines per stock |
| 🔌 **Trading 212 API** | Real order execution (market, limit, stop) |
| 📰 **NewsAPI** | Free-tier headline fetching for sentiment input |
| 💾 **SQLite Database** | Full trade audit trail & P&L tracking |
| 🖥️ **Rich Dashboard** | Beautiful live-updating terminal UI |
| 📱 **Telegram Alerts** | Instant buy/sell notifications |
| 🧪 **Dry-Run Mode** | Paper-trade with zero risk (default) |
| ⏰ **Auto-Scheduler** | Scans at 9:00 AM CET every weekday |
| 🪟 **Windows Autostart** | Launches on boot via Startup folder or Task Scheduler |

---

## 📁 Project Structure

```
StockBot/
├── main.py                 # Entry point – CLI, scheduler, dashboard
├── config.py               # Loads .env, typed settings
├── setup_autostart.py      # Windows auto-start installer
├── requirements.txt        # Python dependencies
├── .env.example            # Template for API keys
├── .env                    # Your actual keys (git-ignored)
├── data/
│   └── trades.db           # SQLite database (auto-created)
└── bot/
    ├── __init__.py
    ├── trading212.py       # Trading 212 REST API client
    ├── news.py             # NewsAPI headline fetcher
    ├── sentiment.py        # OpenAI GPT-4o-mini sentiment analyzer
    ├── indicators.py       # RSI, MACD, Bollinger Bands (via yfinance + ta)
    ├── strategy.py         # Buy/sell decision engine
    ├── database.py         # SQLite trade & scan persistence
    ├── dashboard.py        # Rich terminal dashboard
    ├── notifier.py         # Telegram bot notifications
    └── scheduler.py        # Daily scheduling logic
```

---

## 🚀 Quick Start

### 1. Prerequisites

- **Python 3.11+** (3.12 recommended)
- A **Trading 212** account with API access enabled
- Free API keys from **OpenAI** and **NewsAPI**

### 2. Install Dependencies

```bash
cd StockBot
python -m pip install -r requirements.txt
```

### 3. Configure API Keys

```bash
copy .env.example .env
```

Open `.env` and fill in your keys:

| Variable | Where to get it |
|---|---|
| `TRADING212_API_KEY` | Trading 212 → Settings → API (beta) |
| `TRADING212_ENV` | `practice` (safe) or `live` (real money) |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `NEWSAPI_KEY` | https://newsapi.org/register (free) |
| `TELEGRAM_BOT_TOKEN` | Message @BotFather on Telegram → `/newbot` |
| `TELEGRAM_CHAT_ID` | See Telegram setup below |

### 4. Run the Bot

```bash
# Default: scheduled mode with live dashboard (dry-run by default)
python main.py

# Run a single scan immediately
python main.py --scan-now

# Force dry-run (override .env)
python main.py --scan-now --dry-run

# View dashboard only (no trading)
python main.py --dashboard

# Custom watchlist
python main.py --scan-now --watchlist AAPL MSFT TSLA NVDA
```

---

## 📈 Trading Strategy

### Buy Signal (ALL must be true)
1. **AI Sentiment = POSITIVE** – GPT-4o-mini rates recent news as bullish
2. **RSI < 40** – Stock is approaching oversold territory
3. **Price at lower Bollinger Band** – Price within 1% of the 20-day lower band

### Sell Signal (ANY triggers a sell)
1. **+5% profit** – Take-profit target hit
2. **-2% loss** – Stop-loss triggered
3. **AI Sentiment = NEGATIVE** – News sentiment has turned bearish

### How It Works
```
9:00 AM CET (weekdays)
    │
    ├─ 1. Check existing positions → sell if TP/SL/sentiment triggers
    │
    └─ 2. Scan 50 tickers:
           ├─ Fetch daily candles (yfinance)
           ├─ Calculate RSI, MACD, Bollinger Bands
           ├─ If RSI < 40 AND at lower BB:
           │     ├─ Fetch news headlines (NewsAPI)
           │     ├─ Analyze sentiment (GPT-4o-mini)
           │     └─ If POSITIVE → BUY (€100 per trade)
           └─ Otherwise → HOLD
```

---

## 📱 Telegram Setup

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the bot token → `TELEGRAM_BOT_TOKEN` in `.env`
4. Send any message to your new bot
5. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Find `"chat":{"id":123456789}` → `TELEGRAM_CHAT_ID` in `.env`

---

## 🪟 Windows Auto-Start

```bash
# Add to Windows Startup folder (starts on login)
python setup_autostart.py install

# Or use Task Scheduler (more reliable)
python setup_autostart.py install --method taskscheduler

# Remove autostart
python setup_autostart.py uninstall
```

---

## ⚙️ Configuration Reference

All settings in `.env`:

| Setting | Default | Description |
|---|---|---|
| `DRY_RUN` | `true` | Paper-trade mode (no real orders) |
| `ORDER_SIZE_EUR` | `100` | EUR value per buy order |
| `TAKE_PROFIT_PCT` | `5.0` | Sell at +5% profit |
| `STOP_LOSS_PCT` | `2.0` | Sell at -2% loss |
| `SCAN_HOUR` | `9` | Hour to scan (in TIMEZONE) |
| `SCAN_MINUTE` | `0` | Minute to scan |
| `TIMEZONE` | `Europe/Berlin` | Timezone for scheduling |
| `TRADING212_ENV` | `practice` | `practice` or `live` |

---

## ⚠️ Disclaimer

> **This bot is for educational purposes.** Trading stocks carries financial risk.
> Always start with `DRY_RUN=true` and the Trading 212 **practice** account.
> The authors are not responsible for any financial losses. Past performance
> of any strategy does not guarantee future results.

---

## 🛠️ Development

```bash
# Run with debug logging
python main.py --scan-now 2>&1 | tee scan.log

# Check the database
python -c "from bot.database import TradeDB; db = TradeDB('data/trades.db'); print(db.get_pnl_summary())"
```

---

**Built with ❤️ using Python, OpenAI, Trading 212, and Rich**
