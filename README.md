# OSINT Suite

A browser-based intelligence tool combining [Holehe](https://github.com/megadose/holehe) and [User Scanner](https://github.com/kaifcodec/user-scanner) — check which platforms an email address or username is registered on, with a clean dark UI, user accounts, and saved searches.

---

## Features

### Holehe — email lookup
- Check an email address against hundreds of platforms via the `holehe` CLI
- Filter to confirmed accounts only
- Export report as JSON or TXT

### User Scanner — email & username lookup
- Scan a username or email across a wide range of sites
- Switch between **Email** and **Username** mode
- Live progress bar with running counts (found, scanned, errors, skipped)
- Optionally include "loud" modules (ones that may notify the target)
- Export report as JSON, CSV, or TXT

### Accounts & saved searches
- Register and log in to save searches for quick re-running
- Saved searches shown in a sidebar — click to re-run, × to delete
- Sessions expire after a configurable period (default 8 hours)

### Telegram Bot & Mini App (TMA)
- **Chat Slash Commands**:
  - `/holhe` — Prompts for target email, scans across 120+ platforms with Holehe, reports found accounts only
  - `/us-email` — Prompts for target email, scans platforms with User Scanner
  - `/us-name` — Prompts for target username, scans platforms with User Scanner
  - `/cancel` — Cancels any active command prompt
  - (Power-user shorthand: e.g. `/holhe target@example.com` or `/us-name johndoe`)
- **Found Results Only**: All chat reports and exports strictly filter to confirmed positive accounts.
- **Interactive Mini App**: Full web UI launched inside Telegram with responsive mobile sidebar drawer and haptic feedback.
- **Embedded Runner**: Runs directly inside `app.py` when `TELEGRAM_BOT_TOKEN` is configured.

---

## Requirements

- Python 3.11+
- PostgreSQL
- Docker (optional)

---

## Setup

### 1. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```dotenv
# Flask
SECRET_KEY=your-secret-key-change-in-production

# PostgreSQL connection string
DATABASE_URL=postgresql://user:password@localhost/osint_suite

# Session lifetime in hours (default: 8)
SESSION_HOURS=8

# Telegram Bot (optional)
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
TELEGRAM_WEBAPP_URL=https://your-domain-or-tunnel.com
```

### 2. Configure Telegram Bot & Mini App (Optional)

1. Create a bot using [@BotFather](https://t.me/botfather) and copy the bot token to `TELEGRAM_BOT_TOKEN`.
2. (Optional) In @BotFather, configure a Web App button:
   - Run `/newapp` or `/setmenubutton`, select your bot, and set the URL to your public HTTPS domain / tunnel.
   - Set `TELEGRAM_WEBAPP_URL` in `.env` to match.
3. Start the application (`python app.py`). The Telegram bot launches automatically as an embedded background worker.

### 3. Run with Docker

```bash
docker build -t osint-suite .
docker run --rm -p 5000:5000 --env-file .env osint-suite
```

### 4. Run without Docker

```bash
pip install -r requirements.txt
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

---

## Configuration

| Variable              | Default                                                | Description                                      |
|-----------------------|--------------------------------------------------------|--------------------------------------------------|
| `SECRET_KEY`          | `dev-secret-key-change-in-production`                  | Flask session signing key                        |
| `DATABASE_URL`        | `postgresql://postgres:postgres@localhost/osint_suite` | PostgreSQL connection string                     |
| `SESSION_HOURS`       | `8`                                                    | How long a login session lasts (hours)           |
| `TELEGRAM_BOT_TOKEN`  | `None`                                                 | Telegram Bot API token (enables bot if set)      |
| `TELEGRAM_WEBAPP_URL` | `None`                                                 | Public HTTPS URL for Telegram Mini App button    |

---

## Stack

- **Backend** — Flask, Flask-Login, Flask-Bcrypt, Flask-SQLAlchemy, psycopg3
- **Bot Engine** — python-telegram-bot (v20+ async)
- **Database** — PostgreSQL
- **Scan engines** — [holehe](https://github.com/megadose/holehe), [user-scanner](https://github.com/kaifcodec/user-scanner)
- **Frontend** — Vanilla HTML/CSS/JS, dark theme, SSE for real-time streaming, Telegram WebApp SDK
