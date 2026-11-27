import sqlite3
from datetime import datetime

from bot import backup
from bot.config import TZ
from bot.db import DB


def test_backup_copies_settings_and_keeps_last_n(tmp_path):
    db = DB(tmp_path / "bot.db")
    db.ensure(111, "Дмитро")
    db.update(111, oblast="chernihiv", queue="1.1")
    db.set_setting("owner", "111")
    folder = tmp_path / "backups"
    for day in range(1, 6):
        path = backup.make_backup(db.c, folder, datetime(2026, 11, day, 4, tzinfo=TZ), keep=3)
    assert path.name == "bot-2026-11-05.db"
    assert [p.name for p in sorted(folder.iterdir())] == [
        "bot-2026-11-03.db", "bot-2026-11-04.db", "bot-2026-11-05.db"]
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT oblast, queue FROM subs WHERE chat_id=111").fetchone() == ("chernihiv", "1.1")
    assert copy.execute("SELECT value FROM settings WHERE key='owner'").fetchone() == ("111",)
    assert backup.summary(copy) == "чатів: 1, ручних графіків: 0"


def test_same_day_backup_overwrites(tmp_path):
    db = DB(tmp_path / "bot.db")
    now = datetime(2026, 11, 27, 4, tzinfo=TZ)
    backup.make_backup(db.c, tmp_path / "b", now, keep=14)
    db.ensure(1, None)
    p = backup.make_backup(db.c, tmp_path / "b", now, keep=14)
    assert len(list((tmp_path / "b").iterdir())) == 1
    assert backup.summary(sqlite3.connect(p)).startswith("чатів: 1")


def test_next_run():
    assert backup.next_run(datetime(2026, 11, 27, 3, 59, tzinfo=TZ), 4) == datetime(2026, 11, 27, 4, tzinfo=TZ)
    assert backup.next_run(datetime(2026, 11, 27, 4, 0, tzinfo=TZ), 4) == datetime(2026, 11, 28, 4, tzinfo=TZ)
    assert backup.next_run(datetime(2026, 11, 27, 15, 30, tzinfo=TZ), 4) == datetime(2026, 11, 28, 4, tzinfo=TZ)
