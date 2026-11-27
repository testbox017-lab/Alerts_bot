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
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sent (
    chat_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    day     TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
CREATE TABLE IF NOT EXISTS alert_log (
    id     INTEGER PRIMARY KEY,
    key    TEXT NOT NULL,
    data   TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts   REAL
);
CREATE INDEX IF NOT EXISTS alert_log_open ON alert_log(key, end_ts);
"""
# столбцы, добавленные после первого запуска: (имя, определение)
SUB_COLUMNS_ADDED = [("summary", "TEXT"), ("quiet", "TEXT")]
SUB_COLS = "chat_id,title,oblast,raion,power,queue,remind,summary,quiet"


@dataclass
class Sub:
    chat_id: int
    title: str | None
    oblast: str | None  # одна или несколько через запятую (до 5)
    raion: str | None
    power: str | None
    queue: str | None  # одна или несколько через запятую: "1.1,3.2"
    remind: int
    summary: str | None = None  # время утренней сводки «07:00» или None — выключена
    quiet: str | None = None    # тихие часы «23:00-07:00» или None

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
        have = {r[1] for r in self.c.execute("PRAGMA table_info(subs)")}
        for name, decl in SUB_COLUMNS_ADDED:
            if name not in have:
                self.c.execute(f"ALTER TABLE subs ADD COLUMN {name} {decl}")
                if name == "summary" and config.DEFAULT_SUMMARY:   # сводка включена и старым чатам
                    self.c.execute("UPDATE subs SET summary=?", (config.DEFAULT_SUMMARY,))
        self.c.commit()

    # ----- подписчики -----
    def get(self, chat_id: int) -> Sub | None:
        r = self.c.execute(f"SELECT {SUB_COLS} FROM subs WHERE chat_id=?", (chat_id,)).fetchone()
        return Sub(*r) if r else None

    def ensure(self, chat_id: int, title: str | None) -> Sub:
        self.c.execute("INSERT INTO subs(chat_id,title,remind,summary) VALUES(?,?,?,?) "
                       "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
                       (chat_id, title, config.DEFAULT_REMIND, config.DEFAULT_SUMMARY or None))
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

    def move_chat(self, old_id: int, new_id: int):
        """Группа стала супергруппой и получила новый chat_id — переносим всё на него."""
        for t in ("subs", "overrides", "sent"):
            self.c.execute(f"UPDATE OR REPLACE {t} SET chat_id=? WHERE chat_id=?", (new_id, old_id))
        self.c.commit()

    def all(self) -> list[Sub]:
        return [Sub(*r) for r in self.c.execute(f"SELECT {SUB_COLS} FROM subs")]

    def used_sources(self) -> set[str]:
        return {r[0] for r in self.c.execute(
            "SELECT DISTINCT power FROM subs WHERE power IS NOT NULL")}

    # ----- общие настройки бота (первый источник тревог, владелец) -----
    def setting(self, key: str) -> str | None:
        r = self.c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_setting(self, key: str, value: str):
        self.c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))
        self.c.commit()

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

    def mark_sent(self, chat_id: int, key: str, day: str, commit: bool = True):
        self.c.execute("INSERT OR IGNORE INTO sent VALUES(?,?,?)", (chat_id, key, day))
        if commit:
            self.c.commit()

    def cleanup(self, before_day: str):
        self.c.execute("DELETE FROM sent WHERE day<?", (before_day,))
        self.c.execute("DELETE FROM overrides WHERE day<?", (before_day,))
        self.c.execute("DELETE FROM sched WHERE day<?", (before_day,))
        self.c.commit()

    # ----- журнал тревог (для статистики и утренней сводки) -----
    def alerts_sync(self, current: dict[str, dict], ts: float, gone_at: float | None = None):
        """Сверить журнал со снимком активных тревог: новые — открыть, пропавшие — закрыть.
        После перезапуска бота так же: тревоги, закончившиеся, пока бот лежал, закрываются
        моментом gone_at (последний удачный опрос до перезапуска)."""
        open_keys = {k for (k,) in self.c.execute("SELECT key FROM alert_log WHERE end_ts IS NULL")}
        for k in open_keys - current.keys():
            self.c.execute("UPDATE alert_log SET end_ts=? WHERE key=? AND end_ts IS NULL",
                           (gone_at or ts, k))
        for k in current.keys() - open_keys:
            a = current[k]
            data = {f: a.get(f) for f in ("location_title", "location_oblast", "location_type",
                                          "location_raion", "alert_type")}
            self.c.execute("INSERT INTO alert_log(key,data,start_ts) VALUES(?,?,?)",
                           (k, json.dumps(data, ensure_ascii=False), ts))
        self.c.execute("INSERT OR REPLACE INTO settings VALUES('alerts_seen', ?)", (str(ts),))
        self.c.commit()

    def alert_rows(self, since: float) -> list[tuple[dict, float, float | None]]:
        return [(json.loads(d), s, e) for d, s, e in self.c.execute(
            "SELECT data,start_ts,end_ts FROM alert_log WHERE end_ts IS NULL OR end_ts>?", (since,))]

    def alert_log_since(self) -> float | None:
        r = self.c.execute("SELECT MIN(start_ts) FROM alert_log").fetchone()
        return r[0] if r else None

    def alert_log_cleanup(self, before: float):
        self.c.execute("DELETE FROM alert_log WHERE end_ts IS NOT NULL AND end_ts<?", (before,))
        self.c.commit()

    def commit(self):
        self.c.commit()
