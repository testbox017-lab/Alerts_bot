"""Тревоги alerts.in.ua: загрузка и фильтр под подписчика (чистые функции)."""

import logging
import time

import aiohttp

from . import config, ukrainealarm

ACTIVE_URL = "https://api.alerts.in.ua/v1/alerts/active.json"

ALERT_TYPES = {
    "air_raid": "🚨 Повітряна тривога",
    "artillery_shelling": "💥 Загроза артобстрілу",
    "urban_fights": "⚔️ Вуличні бої",
    "chemical": "☣️ Хімічна загроза",
    "nuclear": "☢️ Радіаційна загроза",
}


SOURCE_NAMES = {"ukrainealarm": "ukrainealarm.com", "alertsinua": "alerts.in.ua",
                "mirror": "ukrainealarm.com (дзеркало siren.pp.ua)"}


log = logging.getLogger("bot")

FALLBACK = "mirror"   # запасной источник, ключ не нужен
FAILS_TO_SWITCH = 3   # столько ошибок основного подряд — переходим на запасной
RETRY_PRIMARY = 300   # раз в столько секунд пробуем вернуться на основной


class _State:
    active = ""            # источник, с которого сейчас берём данные
    fails = 0              # ошибок основного подряд
    next_primary_try = 0.0


state = _State()


def source_name(source: str | None = None) -> str:
    """Человекочитаемое название источника (по умолчанию — того, что работает сейчас)."""
    src = source or state.active or config.ALERTS_SOURCE
    return SOURCE_NAMES.get(src, src)


async def _fetch_from(session: aiohttp.ClientSession, source: str) -> dict[str, dict]:
    if source == "alertsinua":
        raw = await fetch_alertsinua(session)
    else:
        token = config.UKRAINEALARM_TOKEN if source == "ukrainealarm" else ""
        raw = await ukrainealarm.fetch_active(session, token)
    # единый ключ (название + тип) — чтобы при смене источника не было ложных тревог/відбоїв
    return {alert_key(a): a for a in raw.values()}


async def fetch_active(session: aiohttp.ClientSession) -> dict[str, dict]:
    """Все активные тревоги по Украине: {ключ: alert}.

    Берём из основного источника (config.ALERTS_SOURCE). После FAILS_TO_SWITCH
    ошибок подряд переходим на зеркало siren.pp.ua и раз в RETRY_PRIMARY секунд
    пробуем вернуться на основной.
    """
    primary = config.ALERTS_SOURCE
    if not state.active:
        state.active = primary
    if primary == FALLBACK:
        return await _fetch_from(session, primary)

    on_fallback = state.active == FALLBACK
    if not on_fallback or time.monotonic() >= state.next_primary_try:
        try:
            res = await _fetch_from(session, primary)
        except Exception as e:  # noqa: BLE001
            state.fails += 1
            if not on_fallback and state.fails < FAILS_TO_SWITCH:
                raise
            if not on_fallback:
                log.warning("источник %s недоступен (%s ошибок подряд, последняя: %r) — "
                            "переключаюсь на %s", source_name(primary), state.fails, e,
                            source_name(FALLBACK))
                state.active = FALLBACK
            state.next_primary_try = time.monotonic() + RETRY_PRIMARY
        else:
            if on_fallback:
                log.warning("источник %s снова работает — возвращаюсь на него", source_name(primary))
            state.active, state.fails = primary, 0
            return res
    return await _fetch_from(session, FALLBACK)


async def fetch_alertsinua(session: aiohttp.ClientSession) -> dict[str, dict]:
    headers = {"Authorization": f"Bearer {config.ALERTS_TOKEN}"}
    async with session.get(ACTIVE_URL, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        data = await r.json()
    return {alert_key(a): a for a in data.get("alerts", [])}


def alert_key(a: dict) -> str:
    """Ключ тревоги, одинаковый для всех источников: название локации + тип."""
    title = _norm(a.get("location_title")).replace("’", "'").replace("ʼ", "'")
    return f'{title}|{a.get("alert_type")}'


def _norm(s: str | None) -> str:
    return (s or "").replace(" ", "").lower()


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
    ob = _norm(oblast)
    if _norm(a.get("location_oblast")) != ob and _norm(title) != ob:
        return False
    filters = parse_filters(raion)
    if not filters or a.get("location_type") == "oblast" or _norm(title) == ob:
        return True
    hay = f'{title} {a.get("location_raion") or ""}'.lower()
    return any(f in hay for f in filters)


def fmt_alert(a: dict) -> str:
    kind = ALERT_TYPES.get(a.get("alert_type"), a.get("alert_type") or "Тривога")
    return f'{kind}: {a.get("location_title")}'


def fmt_clear(a: dict) -> str:
    return f'✅ Відбій: {a.get("location_title")}'
