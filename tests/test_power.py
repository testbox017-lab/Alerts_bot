from datetime import datetime

import pytest

from bot import power
from bot.config import TZ
from bot.db import DB


def test_slots_to_intervals_halves_and_merge():
    slots = {str(h): "yes" for h in range(1, 25)}
    slots.update({"5": "second", "6": "no", "7": "no", "8": "first", "24": "no"})
    assert power.slots_to_intervals(slots) == [["04:30", "07:30"], ["23:00", "24:00"]]


def test_parse_source_and_dates():
    d = {"fact": {"update": "20.11.2026 10:00", "data": {
        # полночь по Киеву и полночь UTC — обе должны дать 20.11
        "1795125600": {"GPV1.1": {"1": "no"}},
        "1795219200": {"GPV1.1": {"24": "no"}},
    }}}
    res, upd = power.parse_source(d)
    assert upd == "20.11.2026 10:00"
    assert res["1.1"]["2026-11-20"] == [["00:00", "01:00"]]
    assert res["1.1"]["2026-11-21"] == [["23:00", "24:00"]]


def test_parse_intervals():
    assert power.parse_intervals(["15-18", "06:00-09:30"]) == [["06:00", "09:30"], ["15:00", "18:00"]]
    assert power.parse_intervals(["22-24"]) == [["22:00", "24:00"]]
    with pytest.raises(ValueError):
        power.parse_intervals(["25-26"])


def test_reminders_merge_midnight():
    by_day = {"2026-11-20": [["23:00", "24:00"]], "2026-11-21": [["00:00", "02:00"]]}
    now = datetime(2026, 11, 20, 23, 50, tzinfo=TZ)
    # в 00:00 нет ни "дадут", ни "выключат" — отрезок непрерывный
    assert power.due_reminders(by_day, now, 30) == []
    now = datetime(2026, 11, 21, 1, 45, tzinfo=TZ)
    [(key, kind, t)] = power.due_reminders(by_day, now, 30)
    assert kind == "on" and t.hour == 2


def test_db_schedule_with_override():
    db = DB(":memory:")
    db.ensure(1, "me")
    db.update(1, power="chernihiv", queue="1.1")
    db.sched_put("chernihiv", "1.1", "2026-11-20", [["06:00", "09:00"]])
    db.sched_put("chernihiv", "1.1", "2026-11-21", [["10:00", "12:00"]])
    db.override_put(1, "2026-11-21", [])
    sub = db.get(1)
    assert db.schedule_for(sub, "2026-11-20") == {
        "2026-11-20": [["06:00", "09:00"]], "2026-11-21": []}
    assert db.used_sources() == {"chernihiv"}
    db.delete(1)
    assert db.get(1) is None
