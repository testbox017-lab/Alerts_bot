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
    return halves_to_intervals(halves)


def halves_to_intervals(halves: list[int]) -> list[Interval]:
    """48 получасов (1 = нет света) -> интервалы ["HH:MM", "HH:MM"]."""

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


# ---------- svitlo.live: одна JSON-выдача на всю страну, получасовые слоты ----------
SVITLO_URL = "https://dtek-api.svitlo-proxy.workers.dev/"
STANDARD_QUEUES = [f"{a}.{b}" for a in range(1, 7) for b in (1, 2)]
# Чернівціобленерго нумерует группы 1–12, остальные — 1.1–6.2
SVITLO_QUEUES = {"chernivetska-oblast": [str(i) for i in range(1, 13)]}


def parse_svitlo(payload: dict, cpu: str) -> dict[str, dict[str, list[Interval]]]:
    """Ответ svitlo.live -> {queue: {iso_day: intervals}} для региона cpu (напр. sumska-oblast).

    Слоты "HH:MM": 1 = свет есть, 2 = отключение, прочее — неизвестно (считаем, что свет есть).
    """
    body = payload.get("body") if isinstance(payload, dict) else None
    if isinstance(body, str):
        payload = json.loads(body)
    region = next((r for r in payload.get("regions", []) if r.get("cpu") == cpu), None)
    if region is None:
        raise ValueError(f"svitlo.live: region {cpu} is missing")
    # очереди есть всегда, даже если графика сейчас нет, — чтобы их можно было выбрать
    res: dict[str, dict[str, list[Interval]]] = {q: {} for q in SVITLO_QUEUES.get(cpu, STANDARD_QUEUES)}
    for q, days in (region.get("schedule") or {}).items():
        for day, slots in (days or {}).items():
            if not isinstance(slots, dict) or not slots:
                continue
            halves = [1 if slots.get(f"{i // 2:02d}:{30 * (i % 2):02d}") == 2 else 0 for i in range(48)]
            res.setdefault(str(q), {})[str(day)] = halves_to_intervals(halves)
    return res


# ---------- Миколаївобленерго: официальный API off.energy.mk.ua ----------
MYKOLAIV_API = "https://off.energy.mk.ua/api"
_MK_TYPES = {"OFF": 1, "SURE_OFF": 1, "PROBABLY_OFF": None, "ENABLE": 0}


def parse_mykolaiv(queues: list, series: list, active: list) -> dict[str, dict[str, list[Interval]]]:
    """Очереди (type 3), получасовые слоты и активные графики -> {queue: {iso_day: intervals}}."""
    group_by_id = {q["id"]: q["name"] for q in queues if re.fullmatch(r"\d+\.\d+", str(q.get("name")))}
    slot_by_id = {}
    for it in series:
        mt = re.match(r"(\d{2}):(\d{2})", str(it.get("start")))
        if mt:
            slot_by_id[it["id"]] = (int(mt[1]) * 60 + int(mt[2])) // 30
    if not group_by_id or not slot_by_id:
        raise ValueError("mykolaiv: no queues or time slots")
    possible = int(config.POSSIBLE_AS_OFF)
    res: dict[str, dict[str, list[Interval]]] = {g: {} for g in group_by_id.values()}
    for sch in active:
        start = datetime.fromisoformat(str(sch.get("from")).replace("Z", "+00:00"))
        day = start.astimezone(TZ).date().isoformat()
        halves = {g: [0] * 48 for g in group_by_id.values()}
        for cell in sch.get("series", []):
            g = group_by_id.get(cell.get("outage_queue_id"))
            i = slot_by_id.get(cell.get("time_series_id"))
            t = cell.get("type")
            if g is None or i is None or t not in _MK_TYPES or not 0 <= i < 48:
                continue
            halves[g][i] = possible if _MK_TYPES[t] is None else _MK_TYPES[t]
        for g, h in halves.items():
            res.setdefault(g, {})[day] = halves_to_intervals(h)
    return res


# off.energy.mk.ua и svitlo-proxy (Cloudflare) отвечают 403 на User-Agent aiohttp/urllib
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; alerts-bot)"}


# ---------- Koly Ye (vkardin/koly): нормализованные графики, в т.ч. Сумы, Волынь, Кировоград ----------
KOLY_URL = "https://vkardin.github.io/koly/v1/regions/{}.json"


def parse_koly(d: dict) -> dict[str, dict[str, list[Interval]]]:
    """{"days": [{"date", "groups": {q: [{"from", "to", "type": off|maybe}]}}]} -> {queue: {day: intervals}}."""
    res: dict[str, dict[str, list[Interval]]] = {q: {} for q in STANDARD_QUEUES}
    for day in d.get("days") or []:
        for q, items in (day.get("groups") or {}).items():
            iv = [[i["from"], i["to"]] for i in items or []
                  if i.get("type") == "off" or (i.get("type") == "maybe" and config.POSSIBLE_AS_OFF)]
            res.setdefault(str(q), {})[str(day["date"])] = sorted(iv)
    return res


def _koly_stamp(d: dict, key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(d[key])
    except (KeyError, TypeError, ValueError):
        return None


async def _get_json(session: aiohttp.ClientSession, url: str):
    async with session.get(url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        return json.loads(await r.text())


async def fetch_source(session: aiohttp.ClientSession, url: str):
    """-> (расписание, текст времени обновления, lastUpdated как datetime или None).

    url — либо ссылка на JSON в формате OE_OUTAGE_DATA/outage-data-ua,
    либо "svitlo:<cpu>" (svitlo.live), "koly:<id>" (Koly Ye), "mykolaiv:" (API Миколаївобленерго).
    У svitlo и mykolaiv нет поля lastUpdated, поэтому время обновления — момент загрузки.
    """
    now_txt = f"{datetime.now(TZ):%d.%m.%Y %H:%M}"
    if url.startswith("svitlo:"):
        return parse_svitlo(await _get_json(session, SVITLO_URL), url.removeprefix("svitlo:")), now_txt, None
    if url.startswith("koly:"):
        d = await _get_json(session, KOLY_URL.format(url.removeprefix("koly:")))
        upd = _koly_stamp(d, "updatedAt")
        # fetchedAt — когда сборщик последний раз сходил к облэнерго: по нему видно, что он жив
        return parse_koly(d), (f"{upd.astimezone(TZ):%d.%m.%Y %H:%M}" if upd else now_txt), _koly_stamp(d, "fetchedAt")
    if url == "mykolaiv:":
        queues = await _get_json(session, f"{MYKOLAIV_API}/outage-queue/by-type/3")
        series = await _get_json(session, f"{MYKOLAIV_API}/schedule/time-series")
        active = await _get_json(session, f"{MYKOLAIV_API}/v2/schedule/active")
        return parse_mykolaiv(queues, series, active), now_txt, None
    d = await _get_json(session, url)
    data, upd = parse_source(d)
    return data, upd, last_updated(d)


def last_updated(d: dict) -> datetime | None:
    """Поле lastUpdated источника (ISO, UTC). Источники обновляют его минимум раз в сутки
    (около 00:05 переходят на новый день), поэтому по нему видно, что сборщик жив."""
    v = d.get("lastUpdated") if isinstance(d, dict) else None
    if isinstance(v, (int, float)):  # у gpv-voe-vinnytsia это unix-время
        return datetime.fromtimestamp(v, TZ)
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
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
