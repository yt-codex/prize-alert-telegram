# prize-alert-telegram

Monitors **Singapore Pools TOTO** and sends a Telegram alert when the **Next Jackpot estimate** exceeds your configured threshold.

## What it does
- Fetches the Singapore Pools archive snippet for TOTO (`toto_next_draw_estimate_en.html`).
- Extracts:
  - **Next Jackpot estimate**
  - **Next Draw** date/time
- Sends Telegram alert only when estimate is strictly greater than the threshold in `config.yaml`.
- Stores last alerted draw in `.state/last_alert.json` to avoid duplicate alerts.

## Setup
1. Create a Telegram bot via BotFather and copy the bot token.
2. Get your chat ID.
3. In GitHub repository secrets, set:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Edit `config.yaml` and set `threshold.amount`.

## Local run
From repo root:

```bash
python -m src.debug_parse
```

```bash
DRY_RUN=1 python -m src.check_prize
```

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python -m src.check_prize
```

Windows CMD:

```bat
set DRY_RUN=1
python -m src.check_prize
```

Windows PowerShell:

```powershell
$env:DRY_RUN="1"
python -m src.check_prize
```

## CI behavior
Workflow: `.github/workflows/prize_alert.yml`
- `python -m src.debug_parse` runs as a smoke check.
- `python -m src.check_prize` runs on `workflow_dispatch` and schedule.
- Telegram credentials are read from GitHub Secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- `.state` directory is restored via `actions/cache` for idempotency.

## Schedule
Cron is UTC-based:
- `0 0 * * 2,6` = Tuesday and Saturday at 00:00 UTC
- Intended run time in Singapore: **08:00 SGT** (Tue + Sat)
