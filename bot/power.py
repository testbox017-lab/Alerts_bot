"""Графики отключений: разбор JSON источников, интервалы, напоминания."""

import json
import re
from datetime import date, datetime, time, timedelta

import aiohttp

from . import config
from .config import TZ

Interval = list[str]  # ["06:00", "09:30"]


def _slot_map() -> dict[str, tuple[int, int]]:
    p = int(config.POSSIBLE_AS_OFF)
    # слот "k" = час (k-1):00–k:00 -> (выкл. 1-я половина, выкл. 2-я половина)
    return {"yes": (0, 0), "no": (1, 1), "first": (1, 0), "second": (0, 1),
            "mfirst": (p, 0), "msecond": (0, p)}


def slots_to_intervals(slots: dict) -> list[Interval]:
    smap = _slot_map()
    halves: list[int] = []
    for h in range(1, 25):
        halves += smap.get(slots.get(str(h), "yes"), (0, 0))

    def hm(x: int) -> str:
        return f"{x // 2:02d}:{30 * (x % 2):02d}"

    out, start = [], None
    for i, off in enumerate(halves + [0]):
        if off and start is None:
            start = i
        elif not off and start is not None:
            out.append([hm(start), hm(i)])
            start = None
    return out


def parse_source(d: dict) -> tuple[dict[str, dict[str, list[Interval]]], str]:
    """JSON источника -> ({queue: {iso_day: intervals}}, время обновления)."""
    res: dict[str, dict[str, list[Interval]]] = {}
    for ts, queues in d.get("fact", {}).get("data", {}).items():
        day = datetime.fromtimestamp(int(ts), TZ).date().isoformat()
        for gpv, slots in queues.items():
            q = gpv.removeprefix("GPV")
            res.setdefault(q, {})[day] = slots_to_intervals(slots)
    return res, d.get("fact", {}).get("update", "")


def queue_sort_key(q: str) -> list[int]:
    return [int(x) for x in q.split(".") if x.isdigit()]


async def fetch_source(session: aiohttp.ClientSession, url: str):
    """-> (расписание, текст времени обновления, lastUpdated как datetime или None)."""
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        d = json.loads(await r.text())
    data, upd = parse_source(d)
    return data, upd, last_updated(d)


def last_updated(d: dict) -> datetime | None:
    """Поле lastUpdated источника (ISO, UTC). Источники обновляют его минимум раз в сутки
    (около 00:05 переходят на новый день), поэтому по нему видно, что сборщик жив."""
    try:
        return datetime.fromisoformat(d["lastUpdated"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def health_problem(now: datetime, last_ok: datetime, updated_at: datetime | None) -> str | None:
    """Что не так с источником графика: "unreachable", "stale" или None.

    unreachable — дольше POWER_UNREACHABLE_MIN не удаётся загрузить JSON;
    stale — lastUpdated старше POWER_STALE_HOURS (сборщик перестал работать).
    """
    if now - last_ok >= timedelta(minutes=config.POWER_UNREACHABLE_MIN):
        return "unreachable"
    if updated_at and now - updated_at >= timedelta(hours=config.POWER_STALE_HOURS):
        return "stale"
    return None


def fmt_health(problem: str | None, name: str, updated_at: datetime | None, site: str | None) -> str:
    check = f"\nПеревір графік на сайті: {site}" if site else "\nПеревір графік на сайті обленерго."
    if problem == "unreachable":
        return (f"⚠️ Не вдається завантажити графік «{name}» вже понад "
                f"{config.POWER_UNREACHABLE_MIN} хв. Графік у боті може бути застарілим.{check}")
    if problem == "stale":
        when = f" (останнє: {updated_at.astimezone(TZ):%d.%m %H:%M})" if updated_at else ""
        return (f"⚠️ Графік «{name}» не оновлювався понад {config.POWER_STALE_HOURS} год{when}. "
                f"Можливо, джерело перестало працювати.{check}")
    return f"✅ Графік «{name}» знову оновлюється."


# ---------- ручной ввод ----------
INTERVAL_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?[-–](\d{1,2})(?::(\d{2}))?$")


def parse_intervals(parts: list[str]) -> list[Interval]:
    out = []
    for p in parts:
        mt = INTERVAL_RE.match(p)
        if not mt:
            raise ValueError(p)
        h1, m1, h2, m2 = (int(x or 0) for x in mt.groups())
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
            raise ValueError(p)
        end = "24:00" if h2 == 24 else f"{h2:02d}:{m2:02d}"
        out.append([f"{h1:02d}:{m1:02d}", end])
    return sorted(out)


# ---------- время ----------
def to_dt(day: str, hhmm: str) -> datetime:
    d = date.fromisoformat(day)
    if hhmm == "24:00":
        return datetime.combine(d + timedelta(days=1), time(0), TZ)
    h, mi = map(int, hhmm.split(":"))
    return datetime.combine(d, time(h, mi), TZ)


def outage_periods(by_day: dict[str, list[Interval]]) -> list[tuple[datetime, datetime]]:
    """Склеивает интервалы всех дней в непрерывные отрезки (23:00–24:00 + 00:00–02:00)."""
    spans = sorted((to_dt(d, a), to_dt(d, b)) for d, iv in by_day.items() for a, b in iv)
    merged: list[list[datetime]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def due_reminders(by_day: dict[str, list[Interval]], now: datetime, before_min: int):
    """События (ключ, kind, момент), до которых осталось 0..before_min минут."""
    out = []
    for s, e in outage_periods(by_day):
        for kind, t in (("off", s), ("on", e)):
            left = (t - now).total_seconds() / 60
            if 0 <= left <= before_min:
                out.append((f"{kind}|{t.isoformat()}", kind, t))
    return out


def fmt_day(day: str, iv: list[Interval]) -> str:
    d = date.fromisoformat(day)
    body = ", ".join(f"{a}–{b}" for a, b in iv) if iv else "без відключень"
    return f"{d:%d.%m}: {body}"
