# Alerts_bot

[Русский](README.md) · **English**

A Telegram bot for Ukraine that tells you, your family or your group chat about:

- 🚨 **air-raid alerts** in a chosen oblast (optionally narrowed down to specific raions/hromadas), from the "Povitriana tryvoha" app API ([ukrainealarm.com](https://api.ukrainealarm.com)) or [alerts.in.ua](https://alerts.in.ua);
- 💡 **scheduled power outages** for your queue, with a reminder N minutes before the power goes off and comes back. Schedules come from the open data collectors [OE_OUTAGE_DATA](https://github.com/yaroslav2901/OE_OUTAGE_DATA) and [outage-data-ua](https://github.com/Baskerville42/outage-data-ua); for Vinnytsia oblast from [gpv-voe-vinnytsia](https://github.com/vn-progr/gpv-voe-vinnytsia), for Mykolaiv from the official API [off.energy.mk.ua](https://off.energy.mk.ua), for Sumy, Volyn and Kirovohrad from the [Koly Ye](https://github.com/vkardin/koly) collector. Kherson and Chernivtsi oblasts have no open source yet.

Every chat (private or group) picks its own oblast and queue with buttons after `/start`, so people in different regions can use the same bot.

Python 3, aiogram 3, SQLite, Docker. Deployed automatically to a VPS by GitHub Actions on every push to `main`.

## Supported power schedules

Kyiv city, Kyiv oblast, Dnipropetrovsk, Odesa (DTEK), Cherkasy, Chernihiv, Kharkiv, Khmelnytskyi, Lviv, Poltava, Ivano-Frankivsk, Rivne, Ternopil, Zakarpattia, Zaporizhzhia and Zhytomyr oblasts.

In other oblasts alerts still work, and the outage schedule can be entered by hand: `/svet 06:00-09:00 15-18`.

## Buttons and commands

After `/start` a menu appears at the bottom of the screen: **💡 Power schedule**, **🚨 Alerts now**, **⚙️ Settings** (oblast, queue, reminders, raions, all with buttons) and **❓ Help**. The bot's interface is in Ukrainian. The same actions are available as commands:

| Command | What it does |
|---|---|
| `/start` | choose oblast and queue |
| `/settings` | current settings with "change" buttons |
| `/status` | active alerts right now |
| `/raion Ніжинський, Бахмацька` | only alerts for the whole oblast plus these raions/hromadas; `/raion clear` for the whole oblast |
| `/svet` | schedule for today and tomorrow (the "📋 All queues" button shows every queue of the provider) |
| `/svet 06:00-09:00 15-18` | manual override for today (`/svet zavtra …` for tomorrow, `/svet -` for no outages, `/svet clear` to remove overrides) |
| `/remind 30` | how many minutes in advance to warn (`0` turns reminders off) |
| `/stop` | unsubscribe |

In groups only admins can change settings. Commands always work; for the menu buttons to work in a group, make the bot a group admin (or disable its privacy mode in @BotFather → `/setprivacy` → Disable).

## Running on a server (Ubuntu + Docker)

Once, on the server:

```bash
# Docker, if it isn't installed yet
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in afterwards

mkdir -p ~/alerts_bot/data && cd ~/alerts_bot
nano .env   # paste the contents of .env.example and fill in the tokens
```

- `BOT_TOKEN`: from [@BotFather](https://t.me/BotFather) (`/newbot`);
- Alerts work **without a key**: by default the bot reads the public mirror of the ukrainealarm.com API (siren.pp.ua). For reliability you can add your own keys. The bot walks the chain alerts.in.ua â ukrainealarm.com â mirror (sources without a key are skipped): after 3 errors in a row it moves to the next one and every 5 minutes tries to return to a higher-priority one.
  - `UKRAINEALARM_TOKEN`: official ukrainealarm.com API, request form at [api.ukrainealarm.com](https://api.ukrainealarm.com);
  - `ALERTS_TOKEN`: [devs.alerts.in.ua](https://devs.alerts.in.ua).
  Put a source first in the chain with `ALERTS_SOURCE=ukrainealarm|alertsinua|mirror`. After editing `.env` run `cd ~/alerts_bot && docker compose up -d --force-recreate`.

Everything else is done by GitHub Actions.

## Auto-deploy

On every push to `main`, the workflow `.github/workflows/deploy.yml`:

1. runs the tests;
2. builds the Docker image and publishes it to `ghcr.io/testbox017-lab/alerts_bot`;
3. copies `docker-compose.yml` to `~/alerts_bot` on the server over SSH and runs `docker compose pull && docker compose up -d`.

Required secrets (GitHub → repository → Settings → Secrets and variables → Actions → New repository secret):

| Secret | Value |
|---|---|
| `SSH_HOST` | server IP or domain |
| `SSH_USER` | user on the server (member of the `docker` group) |
| `SSH_KEY` | private SSH key (the full text, including `-----BEGIN…`) |
| `SSH_PORT` | optional, defaults to 22 |

A dedicated deploy key:

```bash
# on your own computer
ssh-keygen -t ed25519 -f deploy_key -N "" -C "github-deploy"
ssh-copy-id -i deploy_key.pub user@server
# contents of deploy_key -> SSH_KEY secret
```

While the secrets are not set, the deploy step is skipped with a warning.

## Optional settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ALERTS_POLL_SECONDS` | 30 | how often to poll the alert source |
| `POWER_POLL_SECONDS` | 600 | how often to refresh power schedules |
| `DB_PATH` | data/bot.db | SQLite database path |
| `DEFAULT_REMIND` | 30 | default reminder lead time, minutes |
| `POSSIBLE_AS_OFF` | 1 | `1` treats "possible outage" slots as outages, `0` ignores them |

## Manual run / development

```bash
cp .env.example .env   # fill it in
docker compose up -d --build
docker compose logs -f
```

Without Docker: `pip install -r requirements-dev.txt && python -m pytest && python -m bot`.

Data (subscribers, schedule cache) is stored in SQLite at `data/bot.db`.
