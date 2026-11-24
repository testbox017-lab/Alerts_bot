"""SQLite: подписчики, ручные правки графика, кэш графиков, отправленные напоминания."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS subs (
    chat_id  INTEGER PRIMARY KEY,
    title    TEXT,
    oblast   TEXT,
    raion    TEXT,
    power    TEXT,
    queue    TEXT,
    remind   INTEGER NOT NULL DEFAULT 30
);
CREATE TABLE IF NOT EXISTS overrides (
    chat_id   INTEGER NOT NULL,
    day       TEXT NOT NULL,
    intervals TEXT NOT NULL,
    PRIMARY KEY (chat_id, day)
);
CREATE TABLE IF NOT EXISTS sched (
    power     TEXT NOT NULL,
    queue     TEXT NOT NULL,
    day       TEXT NOT NULL,
    intervals TEXT NOT NULL,
    PRIMARY KEY (power, queue, day)
);
CREATE TABLE IF NOT EXISTS sent (
    chat_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    day     TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
"""


@dataclass
class Sub:
    chat_id: int
    title: str | None
    oblast: str | None  # одна или несколько через запятую (до 5)
    raion: str | None
    power: str | None
    queue: str | None  # одна или несколько через запятую: "1.1,3.2"
    remind: int

    @property
    def oblasts(self) -> list[str]:
        return [o for o in (self.oblast or "").split(",") if o]

    @property
    def queues(self) -> list[str]:
        return [q for q in (self.queue or "").split(",") if q]


class DB:
    def __init__(self, path: Path | str = config.DB_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path)
        self.c.executescript(SCHEMA)

    # ----- подписчики -----
    def get(self, chat_id: int) -> Sub | None:
        r = self.c.execute("SELECT chat_id,title,oblast,raion,power,queue,remind "
                           "FROM subs WHERE chat_id=?", (chat_id,)).fetchone()
        return Sub(*r) if r else None

    def ensure(self, chat_id: int, title: str | None) -> Sub:
        self.c.execute("INSERT INTO subs(chat_id,title,remind) VALUES(?,?,?) "
                       "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
                       (chat_id, title, config.DEFAULT_REMIND))
        self.c.commit()
        return self.get(chat_id)

    def update(self, chat_id: int, **fields):
        cols = ",".join(f"{k}=?" for k in fields)
        self.c.execute(f"UPDATE subs SET {cols} WHERE chat_id=?", (*fields.values(), chat_id))
        self.c.commit()

    def delete(self, chat_id: int):
        for t in ("subs", "overrides", "sent"):
            self.c.execute(f"DELETE FROM {t} WHERE chat_id=?", (chat_id,))
        self.c.commit()

    def all(self) -> list[Sub]:
        return [Sub(*r) for r in self.c.execute(
            "SELECT chat_id,title,oblast,raion,power,queue,remind FROM subs")]

    def used_sources(self) -> set[str]:
        return {r[0] for r in self.c.execute(
            "SELECT DISTINCT power FROM subs WHERE power IS NOT NULL")}

    # ----- кэш графиков из источников -----
    def sched_get(self, power: str, queue: str) -> dict[str, list]:
        return {d: json.loads(iv) for d, iv in self.c.execute(
            "SELECT day,intervals FROM sched WHERE power=? AND queue=?", (power, queue))}

    def sched_day(self, power: str, day: str) -> dict[str, list]:
        """Все очереди источника на день: {queue: intervals}."""
        return {q: json.loads(iv) for q, iv in self.c.execute(
            "SELECT queue,intervals FROM sched WHERE power=? AND day=?", (power, day))}

    def sched_has(self, power: str) -> bool:
        return self.c.execute("SELECT 1 FROM sched WHERE power=? LIMIT 1", (power,)).fetchone() is not None

    def sched_put(self, power: str, queue: str, day: str, intervals: list):
        self.c.execute("INSERT OR REPLACE INTO sched VALUES(?,?,?,?)",
                       (power, queue, day, json.dumps(intervals)))

    # ----- ручные правки -----
    def overrides(self, chat_id: int) -> dict[str, list]:
        return {d: json.loads(iv) for d, iv in self.c.execute(
            "SELECT day,intervals FROM overrides WHERE chat_id=?", (chat_id,))}

    def override_put(self, chat_id: int, day: str, intervals: list):
        self.c.execute("INSERT OR REPLACE INTO overrides VALUES(?,?,?)",
                       (chat_id, day, json.dumps(intervals)))
        self.c.commit()

    def override_clear(self, chat_id: int, day: str | None = None):
        if day:
            self.c.execute("DELETE FROM overrides WHERE chat_id=? AND day=?", (chat_id, day))
        else:
            self.c.execute("DELETE FROM overrides WHERE chat_id=?", (chat_id,))
        self.c.commit()

    # ----- итоговый график подписчика -----
    def schedule_for(self, sub: Sub, from_day: str, queue: str | None = None) -> dict[str, list]:
        """График одной очереди (по умолчанию первой выбранной) с ручными правками чата."""
        queue = queue or (sub.queues[0] if sub.queues else None)
        base = self.sched_get(sub.power, queue) if sub.power and queue else {}
        base.update(self.overrides(sub.chat_id))
        return {d: iv for d, iv in sorted(base.items()) if d >= from_day}

    def schedules_for(self, sub: Sub, from_day: str) -> dict[str, dict[str, list]]:
        """{queue: {day: intervals}} по всем выбранным очередям (без очереди — только ручные)."""
        if not sub.queues:
            manual = self.schedule_for(sub, from_day)
            return {"": manual} if manual else {}
        return {q: self.schedule_for(sub, from_day, q) for q in sub.queues}

    # ----- напоминания -----
    def was_sent(self, chat_id: int, key: str) -> bool:
        return self.c.execute("SELECT 1 FROM sent WHERE chat_id=? AND key=?",
                              (chat_id, key)).fetchone() is not None

    def mark_sent(self, chat_id: int, key: str, day: str):
        self.c.execute("INSERT OR IGNORE INTO sent VALUES(?,?,?)", (chat_id, key, day))
        self.c.commit()

    def cleanup(self, before_day: str):
        self.c.execute("DELETE FROM sent WHERE day<?", (before_day,))
        self.c.execute("DELETE FROM overrides WHERE day<?", (before_day,))
        self.c.execute("DELETE FROM sched WHERE day<?", (before_day,))
        self.c.commit()

    def commit(self):
        self.c.commit()
