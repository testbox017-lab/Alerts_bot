import os
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Kyiv")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALERTS_TOKEN = os.environ.get("ALERTS_TOKEN", "")
DB_PATH = Path(os.environ.get("DB_PATH", "data/bot.db"))

# alerts.in.ua просит не чаще ~8 запросов в минуту на токен
ALERTS_POLL_SECONDS = int(os.environ.get("ALERTS_POLL_SECONDS", "30"))
POWER_POLL_SECONDS = int(os.environ.get("POWER_POLL_SECONDS", "600"))
DEFAULT_REMIND = int(os.environ.get("DEFAULT_REMIND", "30"))
# "можливе відключення" (mfirst/msecond) считать отключением
POSSIBLE_AS_OFF = os.environ.get("POSSIBLE_AS_OFF", "1") == "1"
