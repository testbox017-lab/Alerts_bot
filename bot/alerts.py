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

FAILS_TO_SWITCH = 3   # столько ошибок подряд — переходим на следующий источник цепочки
RETRY_PRIMARY = 300   # раз в столько секунд пробуем вернуться на более приоритетный


class _State:
    idx = 0                # индекс текущего источника в config.ALERTS_CHAIN
    fails = 0              # ошибок текущего источника подряд
    next_retry = 0.0


state = _State()


def source_name(source: str | None = None) -> str:
    """Человекочитаемое название источника (по умолчанию — того, что работает сейчас)."""
    chain = config.ALERTS_CHAIN
    src = source or chain[min(state.idx, len(chain) - 1)]
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

    Источники идут по цепочке config.ALERTS_CHAIN (например alerts.in.ua →
    ukrainealarm.com → зеркало siren.pp.ua). После FAILS_TO_SWITCH ошибок подряд
    переходим на следующий, раз в RETRY_PRIMARY секунд пробуем вернуться на
    более приоритетные.
    """
    chain = config.ALERTS_CHAIN
    state.idx = min(state.idx, len(chain) - 1)
    if state.idx > 0 and time.monotonic() >= state.next_retry:
        state.next_retry = time.monotonic() + RETRY_PRIMARY
        for i in range(state.idx):
            try:
                res = await _fetch_from(session, chain[i])
            except Exception:  # noqa: BLE001
                continue
            log.warning("источник %s снова работает — переключаюсь на него", source_name(chain[i]))
            state.idx, state.fails = i, 0
            return res
    cur = chain[state.idx]
    try:
        res = await _fetch_from(session, cur)
    except Exception as e:
        state.fails += 1
        if state.fails < FAILS_TO_SWITCH or state.idx == len(chain) - 1:
            raise
        state.idx, state.fails = state.idx + 1, 0
        state.next_retry = time.monotonic() + RETRY_PRIMARY
        log.warning("источник %s недоступен (%s ошибок подряд, последняя: %r) — переключаюсь на %s",
                    source_name(cur), FAILS_TO_SWITCH, e, source_name(chain[state.idx]))
        return await _fetch_from(session, chain[state.idx])
    state.fails = 0
    return res


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


def split_oblasts(oblast: str | None) -> list[str]:
    """Области чата хранятся через запятую: «Чернігівська область,Київська область»."""
    return [o.strip() for o in (oblast or "").split(",") if o.strip()]


def matches(a: dict, oblast: str | None, raion: str | None) -> bool:
    """Тревога касается подписчика?

    oblast — одна или несколько областей через запятую.
    Без фильтра района — все тревоги этих областей. С фильтром (через запятую,
    например «Ніжинський, Бахмацька») — тревоги на всю область плюс те,
    где фильтр встречается в названии локации или района.
    """
    obs = split_oblasts(oblast)
    if len(obs) > 1:
        return any(matches(a, o, raion) for o in obs)
    if not obs:
        return False
    title = a.get("location_title", "")
    ob = _norm(obs[0])
    if _norm(a.get("location_oblast")) != ob and _norm(title) != ob:
        return False
    filters = parse_filters(raion)
    if not filters or a.get("location_type") == "oblast" or _norm(title) == ob:
        return True
    hay = f'{title} {a.get("location_raion") or ""}'.lower()
    return any(f in hay for f in filters)


def place(a: dict, with_oblast: bool = False) -> str:
    """Название локации; при нескольких областях у чата — с областью в скобках."""
    title = a.get("location_title") or ""
    ob = a.get("location_oblast") or ""
    if with_oblast and ob and _norm(ob) != _norm(title):
        return f'{title} ({ob.replace(" область", " обл.")})'
    return title


def fmt_alert(a: dict, with_oblast: bool = False) -> str:
    kind = ALERT_TYPES.get(a.get("alert_type"), a.get("alert_type") or "Тривога")
    return f'{kind}: {place(a, with_oblast)}'


def fmt_clear(a: dict, with_oblast: bool = False) -> str:
    return f'✅ Відбій: {place(a, with_oblast)}'
