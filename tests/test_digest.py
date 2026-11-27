import asyncio
import sqlite3
from datetime import datetime

import pytest

from bot import digest, main
from bot.config import TZ
from bot.db import DB

CH = "Чернігівська область"
KY = "Київська область"


def at(h, m=0, day=27):
    return datetime(2026, 11, day, h, m, tzinfo=TZ)


def alert(title, oblast, typ="oblast", raion=None):
    return {"location_title": title, "location_oblast": oblast, "location_type": typ,
            "location_raion": raion, "alert_type": "air_raid"}


def test_parse_time_and_quiet():
    assert digest.parse_hhmm("7") == "07:00"
    assert digest.parse_hhmm("6.30") == "06:30"
    assert digest.parse_quiet("23-7") == "23:00-07:00"
    assert digest.parse_quiet("22:30 – 06:00") == "22:30-06:00"
    for bad in ("24", "7:75", "abc"):
        with pytest.raises(ValueError):
            digest.parse_hhmm(bad)
    with pytest.raises(ValueError):
        digest.parse_quiet("7-7")


def test_in_quiet_over_midnight_and_same_day():
    q = "23:00-07:00"
    assert digest.in_quiet(q, at(23))
    assert digest.in_quiet(q, at(3, 15))
    assert not digest.in_quiet(q, at(7))
    assert not digest.in_quiet(q, at(12))
    assert digest.in_quiet("01:00-05:00", at(2))
    assert not digest.in_quiet("01:00-05:00", at(23))
    assert not digest.in_quiet(None, at(3))


def test_summary_due_window():
    assert not digest.summary_due("07:00", at(6, 59))
    assert digest.summary_due("07:00", at(7))
    assert digest.summary_due("07:00", at(7, 59))
    assert not digest.summary_due("07:00", at(8))      # включили позже — до завтра
    assert not digest.summary_due(None, at(7))


def test_stats_merge_overlaps_and_filter():
    ts = lambda h, m=0: at(h, m).timestamp()  # noqa: E731
    rows = [
        (alert(CH, CH), ts(1), ts(2)),                                     # вся область
        (alert("Ніжинський район", CH, "raion"), ts(1, 30), ts(2, 30)),    # пересекается
        (alert("Корюківський район", CH, "raion"), ts(5), ts(5, 20)),      # не в фильтре
        (alert(KY, KY), ts(3), None),                                      # идёт сейчас
    ]
    now = at(4)
    n, sec = digest.oblast_stats(rows, CH, None, at(0).timestamp(), now.timestamp())
    assert (n, sec) == (1, 90 * 60)
    n, sec = digest.oblast_stats(rows, KY, None, at(0).timestamp(), now.timestamp())
    assert (n, sec) == (1, 60 * 60)
    # с фильтром района Корюківський (после 4:00 его тоже нет) — только «вся область»
    text = digest.stats_text(rows, [CH, KY], "Корюківський", at(6), 1, ts(0))
    assert "Чернігівська обл.: 2 тривоги, разом 1 год 20 хв" in text
    assert "Київська обл.: 1 тривога, разом 3 год" in text
    assert "не враховано" not in text
    # журнал начат позже начала периода — предупреждаем
    assert "не враховано" in digest.stats_text(rows, [CH], None, at(6), 7, ts(1))


def test_night_text():
    rows = [(alert(CH, CH), at(23, day=26).timestamp(), at(23, 45, day=26).timestamp()),
            (alert(CH, CH), at(20, day=26).timestamp(), at(21, day=26).timestamp())]  # до 22:00
    assert digest.night_text(rows, [CH], None, at(7)) == \
        "🌙 За ніч (з 22:00): 1 тривога, разом 45 хв"
    assert digest.night_text([], [CH], None, at(7)) == "🌙 За ніч (з 22:00): тривог не було"


def test_count_word():
    assert [digest.count_word(n) for n in (1, 2, 5, 11, 21, 22, 25)] == \
        ["тривога", "тривоги", "тривог", "тривог", "тривога", "тривоги", "тривог"]


def test_db_migration_adds_columns_and_enables_summary(tmp_path):
    path = tmp_path / "bot.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE subs (chat_id INTEGER PRIMARY KEY, title TEXT, oblast TEXT, "
              "raion TEXT, power TEXT, queue TEXT, remind INTEGER NOT NULL DEFAULT 30)")
    c.execute("INSERT INTO subs(chat_id, oblast) VALUES (1, ?)", (CH,))
    c.commit()
    c.close()
    db = DB(path)
    sub = db.get(1)
    assert sub.summary == "07:00" and sub.quiet is None
    db.update(1, summary=None, quiet="23:00-07:00")
    db2 = DB(path)                 # повторный запуск не трогает выбор пользователя
    assert db2.get(1).summary is None and db2.get(1).quiet == "23:00-07:00"


def test_alert_log_sync_and_restart():
    db = DB(":memory:")
    a = alert(CH, CH)
    db.alerts_sync({"k": a}, 100)
    db.alerts_sync({"k": a}, 160)              # та же тревога — без дублей
    db.alerts_sync({}, 220)
    db.alerts_sync({"k": a}, 300)
    # перезапуск: тревога пропала, пока бот лежал, — закрываем последним опросом
    db.alerts_sync({}, 900, gone_at=float(db.setting("alerts_seen")))
    rows = sorted((s, e) for _, s, e in db.alert_rows(0))
    assert rows == [(100, 220), (300, 300)]
    assert db.alert_log_since() == 100
    db.alert_log_cleanup(250)
    assert len(db.alert_rows(0)) == 1


def test_summary_text_and_quiet_send(monkeypatch):
    monkeypatch.setattr(main, "db", DB(":memory:"), raising=False)
    sub = main.db.ensure(5, "t")
    main.db.update(5, oblast=CH, power="chernihiv", queue="1.1", quiet="23:00-07:00")
    main.db.sched_put("chernihiv", "1.1", "2026-11-27", [["06:00", "09:00"], ["15:00", "18:00"]])
    main.db.alerts_sync({"k": alert(CH, CH)}, at(2).timestamp())
    main.db.alerts_sync({}, at(3).timestamp())
    monkeypatch.setattr(main, "last_alerts", {})
    sub = main.db.get(5)
    text = main.summary_text(sub, at(7))
    assert "Зведення на 27.11" in text
    assert "💡 Відключення (черга 1.1): 06:00–09:00, 15:00–18:00" in text
    assert "1 тривога, разом 1 год" in text
    assert "🟢 Зараз тривог немає" in text

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((text, kw["disable_notification"]))

    monkeypatch.setattr(main, "bot", FakeBot(), raising=False)
    monkeypatch.setattr(main, "datetime", type("D", (datetime,), {
        "now": staticmethod(lambda tz=None: at(3))}))
    asyncio.run(main.send(5, "✅ Відбій: x"))
    asyncio.run(main.send(5, main.Loud("🚨 Повітряна тривога: x")))
    assert sent == [("✅ Відбій: x", True), ("🚨 Повітряна тривога: x", False)]
