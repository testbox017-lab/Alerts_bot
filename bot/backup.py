"""Резервные копии SQLite: раз в сутки копия в data/backups и (по желанию) владельцу в Telegram.

Копия делается через sqlite3 backup API — безопасно, пока бот пишет в базу.
Кэш графиков (sched) и отметки отправленных напоминаний (sent) в копию попадают,
но они восстанавливаются сами; главное — subs, overrides и settings.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

PREFIX = "bot-"
SUFFIX = ".db"


def make_backup(conn: sqlite3.Connection, folder: Path, now: datetime, keep: int) -> Path:
    """Копия базы в folder/bot-YYYY-MM-DD.db; хранит последние keep копий."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{PREFIX}{now:%Y-%m-%d}{SUFFIX}"
    tmp = path.with_suffix(".tmp")
    dst = sqlite3.connect(tmp)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    tmp.replace(path)
    prune(folder, keep)
    return path


def prune(folder: Path, keep: int) -> list[Path]:
    """Удаляет старые копии, оставляя keep самых свежих (по имени = по дате)."""
    files = sorted(folder.glob(f"{PREFIX}*{SUFFIX}"))
    old = files[:-keep] if keep > 0 else []
    for f in old:
        f.unlink(missing_ok=True)
    return old


def next_run(now: datetime, hour: int) -> datetime:
    """Ближайший момент hour:00 после now (в том же часовом поясе)."""
    t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def summary(conn: sqlite3.Connection) -> str:
    """Короткая подпись к файлу: сколько чатов и ручных графиков в копии."""
    subs = conn.execute("SELECT COUNT(*) FROM subs").fetchone()[0]
    ov = conn.execute("SELECT COUNT(*) FROM overrides").fetchone()[0]
    return f"чатів: {subs}, ручних графіків: {ov}"
