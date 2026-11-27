"""Утренняя сводка, тихие часы и статистика тревог (чистые функции, без сети и БД)."""

import re
from datetime import datetime, time, timedelta

from . import alerts

# ---------- время ----------
_HHMM_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?$")


def parse_hhmm(s: str) -> str:
    """«7», «7:30», «07.30» → «07:30». ValueError, если не время."""
    m = _HHMM_RE.match(s.strip())
    if not m:
        raise ValueError(s)
    h, mi = int(m[1]), int(m[2] or 0)
    if h > 23 or mi > 59:
        raise ValueError(s)
    return f"{h:02d}:{mi:02d}"


def parse_quiet(s: str) -> str:
    """«23-7», «23:00–07:30» → «23:00-07:30»."""
    parts = re.split(r"\s*[-–]\s*", s.strip())
    if len(parts) != 2:
        raise ValueError(s)
    a, b = parse_hhmm(parts[0]), parse_hhmm(parts[1])
    if a == b:
        raise ValueError(s)
    return f"{a}-{b}"


def _t(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m)


def in_quiet(spec: str | None, now: datetime) -> bool:
    """Попадает ли момент в тихие часы «23:00-07:00» (интервал может идти через полночь)."""
    if not spec:
        return False
    a, b = (_t(x) for x in spec.split("-"))
    t = now.time().replace(second=0, microsecond=0)
    return a <= t < b if a < b else t >= a or t < b


SUMMARY_WINDOW = timedelta(hours=1)   # если бот лежал, сводку шлём не позже чем через час


def summary_due(at: str | None, now: datetime) -> bool:
    """Пора слать утреннюю сводку: время наступило, но прошло не больше часа.
    (Если включили сводку в 10:00 на 07:00 — сегодня не шлём, придёт завтра.)"""
    if not at:
        return False
    start = datetime.combine(now.date(), _t(at), now.tzinfo)
    return start <= now < start + SUMMARY_WINDOW


# ---------- статистика тревог ----------
Row = tuple[dict, float, float | None]   # (тревога как у alerts.*, начало ts, конец ts или None)


def union(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def oblast_stats(rows: list[Row], oblast: str, raion: str | None,
                 since: float, until: float) -> tuple[int, float]:
    """(сколько раз была тревога, сколько секунд всего) в области/её районах из фильтра.
    Пересекающиеся тревоги (область + район) склеиваются в одну."""
    spans = []
    for a, s, e in rows:
        e = until if e is None else e
        if e <= since or s >= until or not alerts.matches(a, oblast, raion):
            continue
        spans.append((max(s, since), min(e, until)))
    merged = union(spans)
    return len(merged), sum(e - s for s, e in merged)


def fmt_duration(sec: float) -> str:
    m = round(sec / 60)
    h, m = divmod(m, 60)
    if h and m:
        return f"{h} год {m} хв"
    return f"{h} год" if h else f"{m} хв"


def count_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "тривога"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "тривоги"
    return "тривог"


def stats_lines(rows: list[Row], oblasts: list[str], raion: str | None,
                since: float, until: float) -> list[str]:
    lines = []
    for ob in oblasts:
        n, sec = oblast_stats(rows, ob, raion, since, until)
        name = ob.replace(" область", " обл.")
        lines.append(f"{name}: {n} {count_word(n)}, разом {fmt_duration(sec)}"
                     if n else f"{name}: тривог не було")
    return lines


def stats_text(rows: list[Row], oblasts: list[str], raion: str | None,
               now: datetime, days: int, since_log: float | None) -> str:
    """Статистика за сегодня (days=1, с 00:00) или за последние 7 дней."""
    if days == 1:
        start = datetime.combine(now.date(), time(0), now.tzinfo)
        title = "📊 Тривоги сьогодні (з 00:00)"
    else:
        start = now - timedelta(days=days)
        title = f"📊 Тривоги за {days} днів"
    since = start.timestamp()
    text = title + ("\n(лише райони з фільтра /raion і тривоги на всю область)" if raion else "")
    text += "\n\n" + "\n".join(stats_lines(rows, oblasts, raion, since, now.timestamp()))
    if since_log is None or since_log > since:
        first = datetime.fromtimestamp(since_log, now.tzinfo) if since_log else now
        text += f"\n\nℹ️ Бот рахує тривоги з {first:%d.%m %H:%M}, раніші не враховано."
    return text


def night_text(rows: list[Row], oblasts: list[str], raion: str | None, now: datetime) -> str:
    """Тревоги за ночь: с 22:00 вчера до сейчас."""
    start = datetime.combine(now.date() - timedelta(days=1), time(22), now.tzinfo)
    lines = stats_lines(rows, oblasts, raion, start.timestamp(), now.timestamp())
    if len(oblasts) == 1:
        lines = [ln.split(": ", 1)[1] for ln in lines]
    return "🌙 За ніч (з 22:00): " + ("\n" if len(lines) > 1 else "") + "\n".join(lines)
