"""Сравнение источников тревог: кто раньше сообщает о тревоге и відбої.

Включается ALERTS_COMPARE=1. Только логирование: рассылка идёт как обычно,
от одного источника по цепочке. Раз в ALERTS_COMPARE_SECONDS параллельно
опрашиваем alerts.in.ua и ukrainealarm.com (оба нужны с ключами) и для каждого
начала/конца тревоги пишем в data/compare.jsonl, какой источник был первым и
насколько. Сравниваем на двух уровнях: точная локация (название + тип) и
область целиком (хоть какая-то тревога этого типа в области) — второй уровень
не зависит от того, как источники называют районы и громады.
Отчёт: команда /compare в боте.
"""

import asyncio
import json
import logging
import statistics
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from . import alerts, config

log = logging.getLogger("bot")

SOURCES = ("alertsinua", "ukrainealarm")
SHORT = {"alertsinua": "alerts.in.ua", "ukrainealarm": "ukrainealarm"}
MATCH_WINDOW = 600   # секунд ждём второй источник, потом считаем событие «только у одного»


def oblast_keys(cur: dict[str, dict]) -> set[str]:
    return {f'{alerts._norm(a.get("location_oblast"))}|{a.get("alert_type")}' for a in cur.values()}


class Comparator:
    """Чистая логика: скармливаем снимки обоих источников, получаем события."""

    def __init__(self):
        self.known: dict[tuple[str, str], set[str] | None] = {
            (lvl, s): None for lvl in ("loc", "obl") for s in SOURCES}
        # (lvl, kind, key) -> {"src": кто первый, "t": когда}
        self.pending: dict[tuple[str, str, str], dict] = {}

    def feed(self, src: str, lvl: str, keys: set[str], t: float) -> list[dict]:
        out: list[dict] = []
        prev = self.known[(lvl, src)]
        self.known[(lvl, src)] = keys
        if prev is not None:
            for kind, changed in (("start", keys - prev), ("end", prev - keys)):
                for key in changed:
                    pk = (lvl, kind, key)
                    p = self.pending.get(pk)
                    if p and p["src"] != src:
                        del self.pending[pk]
                        out.append({"lvl": lvl, "kind": kind, "key": key, "first": p["src"],
                                    "delay": round(t - p["t"], 1), "t": p["t"]})
                    elif not p:
                        self.pending[pk] = {"src": src, "t": t}
        return out

    def expire(self, t: float) -> list[dict]:
        out = []
        for pk, p in list(self.pending.items()):
            if t - p["t"] > MATCH_WINDOW:
                del self.pending[pk]
                lvl, kind, key = pk
                out.append({"lvl": lvl, "kind": kind, "key": key, "first": p["src"],
                            "only": True, "t": p["t"]})
        return out


def log_path() -> Path:
    return config.DB_PATH.parent / "compare.jsonl"


def _write(events: list[dict]):
    if not events:
        return
    with log_path().open("a", encoding="utf-8") as f:
        for e in events:
            e["at"] = datetime.fromtimestamp(e.pop("t"), config.TZ).isoformat(timespec="seconds")
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def retry_after(e: BaseException) -> float:
    """Сколько секунд ждать после 429 (по заголовку Retry-After, иначе 60)."""
    if not isinstance(e, aiohttp.ClientResponseError) or e.status != 429:
        return 0.0
    try:
        return max(1.0, float((e.headers or {}).get("Retry-After", 60)))
    except ValueError:
        return 60.0


async def _timed(session: aiohttp.ClientSession, src: str):
    res = await alerts._fetch_from(session, src)
    return res, time.time()


async def compare_loop(session: aiohttp.ClientSession):
    missing = [s for s, tok in (("alertsinua", config.ALERTS_TOKEN),
                                ("ukrainealarm", config.UKRAINEALARM_TOKEN)) if not tok]
    if missing:
        log.warning("ALERTS_COMPARE: нет ключа для %s — сравнение выключено", missing)
        return
    log.info("ALERTS_COMPARE: сравниваю источники раз в %s с, лог %s",
             config.ALERTS_COMPARE_SECONDS, log_path())
    cmp = Comparator()
    pause_until = {s: 0.0 for s in SOURCES}   # не трогаем источник, пока не истёк Retry-After
    while True:
        now = time.time()
        active = [s for s in SOURCES if now >= pause_until[s]]
        results = await asyncio.gather(*(_timed(session, s) for s in active),
                                       return_exceptions=True)
        events = []
        for src, r in zip(active, results):
            if isinstance(r, BaseException):
                # ошибка ≠ «все тревоги закончились»: просто пропускаем этот снимок
                wait = retry_after(r)
                if wait:
                    pause_until[src] = time.time() + wait
                log.info("ALERTS_COMPARE: %s ошибка: %s%s", src, alerts.err_text(r),
                         f", пауза {wait:.0f} с" if wait else "")
                continue
            cur, t = r
            events += cmp.feed(src, "loc", set(cur), t)
            events += cmp.feed(src, "obl", oblast_keys(cur), t)
        events += cmp.expire(time.time())
        try:
            _write(events)
        except OSError as e:
            log.warning("ALERTS_COMPARE: не могу записать лог: %s", e)
        await asyncio.sleep(config.ALERTS_COMPARE_SECONDS)


def report(lines: list[str]) -> str:
    events = [json.loads(x) for x in lines if x.strip()]
    if not events:
        return "Даних ще немає. Почекай кілька тривог."
    first_at = events[0].get("at", "")[:16].replace("T", " ")
    parts = [f"Порівняння джерел з {first_at} (подій: {len(events)})",
             f"Точність ±{config.ALERTS_COMPARE_SECONDS} с (інтервал опитування)."]
    titles = {("obl", "start"): "Області, початок тривоги", ("obl", "end"): "Області, відбій",
              ("loc", "start"): "Локації, початок тривоги", ("loc", "end"): "Локації, відбій"}
    for (lvl, kind), title in titles.items():
        ev = [e for e in events if e["lvl"] == lvl and e["kind"] == kind]
        if not ev:
            continue
        both = [e for e in ev if not e.get("only")]
        ties = [e for e in both if e["delay"] < config.ALERTS_COMPARE_SECONDS]
        parts.append(f"\n{title}: {len(ev)}")
        if ties:
            parts.append(f"  одночасно (різниця < інтервалу): {len(ties)}")
        for src in SOURCES:
            won = [e["delay"] for e in both if e["first"] == src and e not in ties]
            if won:
                parts.append(f"  {SHORT[src]} раніше: {len(won)}, медіана {statistics.median(won):.0f} с, "
                             f"макс {max(won):.0f} с")
        for src in SOURCES:
            only = sum(1 for e in ev if e.get("only") and e["first"] == src)
            if only:
                parts.append(f"  лише {SHORT[src]} (інше не підтвердило за 10 хв): {only}")
    return "\n".join(parts)


def report_from_file() -> str:
    try:
        return report(log_path().read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return "Лог порівняння ще порожній."
