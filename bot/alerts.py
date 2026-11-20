"""Тревоги alerts.in.ua: загрузка и фильтр под подписчика (чистые функции)."""

import aiohttp

from . import config

ACTIVE_URL = "https://api.alerts.in.ua/v1/alerts/active.json"

ALERT_TYPES = {
    "air_raid": "🚨 Повітряна тривога",
    "artillery_shelling": "💥 Загроза артобстрілу",
    "urban_fights": "⚔️ Вуличні бої",
    "chemical": "☣️ Хімічна загроза",
    "nuclear": "☢️ Радіаційна загроза",
}


async def fetch_active(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Все активные тревоги по Украине: {ключ: alert}."""
    headers = {"Authorization": f"Bearer {config.ALERTS_TOKEN}"}
    async with session.get(ACTIVE_URL, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        data = await r.json()
    return {alert_key(a): a for a in data.get("alerts", [])}


def alert_key(a: dict) -> str:
    return f'{a.get("location_uid") or a.get("location_title")}|{a.get("alert_type")}'


def parse_filters(raion: str | None) -> list[str]:
    return [f.strip().lower() for f in (raion or "").split(",") if f.strip()]


def matches(a: dict, oblast: str | None, raion: str | None) -> bool:
    """Тревога касается подписчика?

    Без фильтра района — все тревоги области. С фильтром (через запятую,
    например «Ніжинський, Бахмацька») — тревоги на всю область плюс те,
    где фильтр встречается в названии локации или района.
    """
    if not oblast:
        return False
    title = a.get("location_title", "")
    if a.get("location_oblast") != oblast and title != oblast:
        return False
    filters = parse_filters(raion)
    if not filters or a.get("location_type") == "oblast" or title == oblast:
        return True
    hay = f'{title} {a.get("location_raion") or ""}'.lower()
    return any(f in hay for f in filters)


def fmt_alert(a: dict) -> str:
    kind = ALERT_TYPES.get(a.get("alert_type"), a.get("alert_type") or "Тривога")
    return f'{kind}: {a.get("location_title")}'


def fmt_clear(a: dict) -> str:
    return f'✅ Відбій: {a.get("location_title")}'
