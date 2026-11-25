import json
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


def test_db_sched_day_all_queues():
    db = DB(":memory:")
    db.sched_put("kyiv", "1.1", "2026-11-20", [["06:00", "09:00"]])
    db.sched_put("kyiv", "2.1", "2026-11-20", [])
    db.sched_put("kyiv", "1.1", "2026-11-21", [["10:00", "12:00"]])
    assert db.sched_day("kyiv", "2026-11-20") == {"1.1": [["06:00", "09:00"]], "2.1": []}


def test_db_multiple_queues_with_override():
    db = DB(":memory:")
    db.ensure(1, "me")
    db.update(1, power="kyiv", queue="1.1,3.1")
    db.sched_put("kyiv", "1.1", "2026-11-20", [["06:00", "09:00"]])
    db.sched_put("kyiv", "3.1", "2026-11-20", [["10:00", "12:00"]])
    sub = db.get(1)
    assert sub.queues == ["1.1", "3.1"]
    assert db.schedules_for(sub, "2026-11-20") == {
        "1.1": {"2026-11-20": [["06:00", "09:00"]]},
        "3.1": {"2026-11-20": [["10:00", "12:00"]]}}
    db.override_put(1, "2026-11-20", [])
    assert db.schedules_for(sub, "2026-11-20") == {"1.1": {"2026-11-20": []}, "3.1": {"2026-11-20": []}}
    db.update(1, queue=None)
    assert db.schedules_for(db.get(1), "2026-11-20") == {"": {"2026-11-20": []}}


def test_health_problem():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
    upd = power.last_updated({"lastUpdated": "2026-11-21T22:05:37.631Z"})
    assert upd == datetime(2026, 11, 21, 22, 5, 37, 631000, tzinfo=timezone.utc)
    assert power.last_updated({}) is None
    assert power.health_problem(now, now - timedelta(minutes=10), upd) is None
    assert power.health_problem(now, now - timedelta(minutes=61), upd) == "unreachable"
    assert power.health_problem(now + timedelta(hours=10), now + timedelta(hours=10), upd) == "stale"
    assert power.health_problem(now, now, None) is None


def test_fmt_health():
    from datetime import datetime, timezone
    upd = datetime(2026, 11, 21, 22, 5, tzinfo=timezone.utc)
    site = "https://chernihivoblenergo.com.ua/blackouts"
    t = power.fmt_health("stale", "Чернігівобленерго", upd, site)
    assert "22.11 00:05" in t and site in t and t.startswith("⚠️")
    assert "понад 60 хв" in power.fmt_health("unreachable", "X", None, None)
    assert power.fmt_health(None, "X", None, None).startswith("✅")


def test_parse_svitlo_region():
    slots = {f"{i // 2:02d}:{30 * (i % 2):02d}": 1 for i in range(48)}
    slots.update({"18:00": 2, "18:30": 2, "19:00": 2, "23:30": 2})
    body = {"regions": [
        {"cpu": "sumska-oblast", "schedule": {"3.2": {"2026-11-25": slots, "2026-11-26": {}}}},
        {"cpu": "chernivetska-oblast", "schedule": {}},
    ]}
    res = power.parse_svitlo({"body": json.dumps(body)}, "sumska-oblast")
    assert res["3.2"] == {"2026-11-25": [["18:00", "19:30"], ["23:30", "24:00"]]}
    assert res["1.1"] == {} and len(res) == 12
    assert power.parse_svitlo(body, "chernivetska-oblast") == {str(i): {} for i in range(1, 13)}
    with pytest.raises(ValueError):
        power.parse_svitlo(body, "volynska-oblast")


def test_parse_mykolaiv():
    queues = [{"id": 14, "name": "1.1"}, {"id": 15, "name": "1.2"}, {"id": 99, "name": "Місто"}]
    series = [{"id": i + 1, "start": f"{i // 2:02d}:{30 * (i % 2):02d}:00"} for i in range(48)]
    active = [{"from": "2026-11-24T22:00:00.000000Z", "series": [
        {"time_series_id": 21, "outage_queue_id": 14, "type": "OFF"},   # 10:00
        {"time_series_id": 22, "outage_queue_id": 14, "type": "OFF"},   # 10:30
        {"time_series_id": 23, "outage_queue_id": 14, "type": "PROBABLY_OFF"},  # 11:00
        {"time_series_id": 30, "outage_queue_id": 15, "type": "ENABLE"},
    ]}]
    res = power.parse_mykolaiv(queues, series, active)
    assert res["1.1"]["2026-11-25"] == [["10:00", "11:30"]]
    assert res["1.2"]["2026-11-25"] == []
    assert "Місто" not in res


def test_last_updated_iso_and_unix():
    assert power.last_updated({"lastUpdated": "2026-11-25T10:00:00Z"}).hour == 10
    assert power.last_updated({"lastUpdated": 1795625516}).tzinfo is not None
    assert power.last_updated({}) is None


def test_parse_koly():
    d = {"days": [{"date": "2026-11-25", "groups": {
        "1.1": [{"from": "12:00", "to": "16:00", "type": "off"}, {"from": "08:00", "to": "10:00", "type": "off"}],
        "1.2": [{"from": "22:00", "to": "24:00", "type": "maybe"}],
        "2.1": [],
    }}]}
    res = power.parse_koly(d)
    assert res["1.1"]["2026-11-25"] == [["08:00", "10:00"], ["12:00", "16:00"]]
    assert res["1.2"]["2026-11-25"] == [["22:00", "24:00"]]  # POSSIBLE_AS_OFF=1 по умолчанию
    assert res["2.1"]["2026-11-25"] == []
    assert res["6.2"] == {} and len(res) == 12
