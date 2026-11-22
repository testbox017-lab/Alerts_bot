import os
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Kyiv")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALERTS_TOKEN = os.environ.get("ALERTS_TOKEN", "")          # alerts.in.ua
UKRAINEALARM_TOKEN = os.environ.get("UKRAINEALARM_TOKEN", "")  # api.ukrainealarm.com
# Источники тревог по приоритету: alertsinua (нужен ALERTS_TOKEN),
# ukrainealarm (нужен UKRAINEALARM_TOKEN), mirror (siren.pp.ua, без ключа, всегда
# последний). Источники без ключа пропускаются. ALERTS_SOURCE ставит выбранный
# источник первым в цепочке.
_AVAILABLE = [s for s, ok in (("alertsinua", ALERTS_TOKEN), ("ukrainealarm", UKRAINEALARM_TOKEN)) if ok]
ALERTS_SOURCE = os.environ.get("ALERTS_SOURCE", "").strip().lower() or (
    _AVAILABLE[0] if _AVAILABLE else "mirror")
ALERTS_CHAIN = list(dict.fromkeys([ALERTS_SOURCE, *_AVAILABLE, "mirror"]))
DB_PATH = Path(os.environ.get("DB_PATH", "data/bot.db"))

# alerts.in.ua просит не чаще ~8 запросов в минуту на токен
ALERTS_POLL_SECONDS = int(os.environ.get("ALERTS_POLL_SECONDS", "30"))
POWER_POLL_SECONDS = int(os.environ.get("POWER_POLL_SECONDS", "600"))
# Предупреждать подписчиков, если график не грузится дольше N минут
# или источник не обновлялся дольше N часов (обычно он обновляется хотя бы раз в сутки)
POWER_UNREACHABLE_MIN = int(os.environ.get("POWER_UNREACHABLE_MIN", "60"))
POWER_STALE_HOURS = int(os.environ.get("POWER_STALE_HOURS", "26"))
DEFAULT_REMIND = int(os.environ.get("DEFAULT_REMIND", "30"))
# "можливе відключення" (mfirst/msecond) считать отключением
POSSIBLE_AS_OFF = os.environ.get("POSSIBLE_AS_OFF", "1") == "1"
