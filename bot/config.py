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
# Первый источник можно сменить и из бота (⚙️ Налаштування → 📡 Джерело тривог),
# выбор хранится в БД и важнее ALERTS_SOURCE.
_AVAILABLE = [s for s, ok in (("alertsinua", ALERTS_TOKEN), ("ukrainealarm", UKRAINEALARM_TOKEN)) if ok]
SOURCES = [*_AVAILABLE, "mirror"]   # из чего можно выбирать первый источник


def make_chain(first: str) -> list[str]:
    return list(dict.fromkeys([first, *SOURCES]))


ALERTS_SOURCE = os.environ.get("ALERTS_SOURCE", "").strip().lower() or SOURCES[0]
ALERTS_CHAIN = make_chain(ALERTS_SOURCE)
# Telegram user id тех, кто может менять источник тревог (через запятую). Если пусто —
# владельцем становится первый, кто отправит боту /source в личке (сохраняется в БД).
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = Path(os.environ.get("DB_PATH", "data/bot.db"))

# alerts.in.ua просит не чаще ~8 запросов в минуту на токен
ALERTS_POLL_SECONDS = int(os.environ.get("ALERTS_POLL_SECONDS", "30"))
# ключ ukrainealarm пускает ~1 запрос в минуту: пока работаем с него, опрашиваем не чаще
UKRAINEALARM_POLL_SECONDS = int(os.environ.get("UKRAINEALARM_POLL_SECONDS", "60"))
# Сравнение alerts.in.ua и ukrainealarm (только лог, рассылка не меняется), см. bot/compare.py.
# 60 с: ключ ukrainealarm отвечает 429 чаще ~1 запроса в минуту (проверено 2026-11-24 на OVH).
ALERTS_COMPARE = os.environ.get("ALERTS_COMPARE", "0") == "1"
ALERTS_COMPARE_SECONDS = int(os.environ.get("ALERTS_COMPARE_SECONDS", "60"))
POWER_POLL_SECONDS = int(os.environ.get("POWER_POLL_SECONDS", "600"))
# Предупреждать подписчиков, если график не грузится дольше N минут
# или источник не обновлялся дольше N часов (обычно он обновляется хотя бы раз в сутки)
POWER_UNREACHABLE_MIN = int(os.environ.get("POWER_UNREACHABLE_MIN", "60"))
POWER_STALE_HOURS = int(os.environ.get("POWER_STALE_HOURS", "26"))
DEFAULT_REMIND = int(os.environ.get("DEFAULT_REMIND", "30"))
# "можливе відключення" (mfirst/msecond) считать отключением
POSSIBLE_AS_OFF = os.environ.get("POSSIBLE_AS_OFF", "1") == "1"

# Рассылка (bot/sender.py): сообщений в секунду на весь бот (лимит Telegram ~30),
# сколько отправок одновременно, сколько попыток при ответе 429 Too Many Requests
SEND_RATE = float(os.environ.get("SEND_RATE", "25"))
SEND_CONCURRENCY = int(os.environ.get("SEND_CONCURRENCY", "20"))
SEND_ATTEMPTS = int(os.environ.get("SEND_ATTEMPTS", "5"))

# Резервная копия базы (bot/backup.py): раз в сутки в BACKUP_HOUR (по Киеву) копия в
# data/backups (хранится BACKUP_KEEP последних) и файл владельцу бота в Telegram
# (ADMIN_IDS или тот, кто первым отправил /source). BACKUP_HOUR=-1 — выключить.
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "4"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))
BACKUP_TELEGRAM = os.environ.get("BACKUP_TELEGRAM", "1") == "1"
