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
    for bad in ("18-06", "10-10"):   # конец раньше начала — через полночь не принимаем
        with pytest.raises(ValueError):
            power.parse_intervals([bad])


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


def test_stamps_without_timezone_are_utc():
    from datetime import timezone
    # без пояса раньше получался naive datetime, и health_problem падал с TypeError
    dt = power.last_updated({"lastUpdated": "2026-11-21T22:05:00"})
    assert dt == datetime(2026, 11, 21, 22, 5, tzinfo=timezone.utc)
    assert power.health_problem(datetime.now(TZ), datetime.now(TZ), dt) == "stale"
    assert power._koly_stamp({"fetchedAt": "2026-11-21T22:05:00"}, "fetchedAt").tzinfo is not None
    assert power._koly_stamp({"fetchedAt": None}, "fetchedAt") is None


def test_move_chat_to_supergroup():
    db = DB(":memory:")
    db.ensure(-1, "group")
    db.update(-1, oblast="Чернігівська область", queue="1.1")
    db.override_put(-1, "2026-11-27", [["06:00", "09:00"]])
    db.mark_sent(-1, "off|x", "2026-11-27")
    db.move_chat(-1, -100)
    assert db.get(-1) is None
    assert db.get(-100).oblast == "Чернігівська область"
    assert db.overrides(-100) == {"2026-11-27": [["06:00", "09:00"]]}
    assert db.was_sent(-100, "off|x")


def test_parse_svitlyachok():
    d = {"region": "chernivtsi", "updated": "2026-11-27T15:34:01+02:00", "checked": "2026-11-27T16:34:01+02:00",
         "days": {"2026-11-27": {
             "1": "1" * 22 + "0000003" + "1" * 19,
             "12": "0" * 4 + "1" * 44,
             "bad": "101",
         }}}
    res = power.parse_svitlyachok(d)
    # 0 — отключение, 3 — «можливе» (POSSIBLE_AS_OFF=1 по умолчанию) -> 11:00–14:30
    assert res["1"]["2026-11-27"] == [["11:00", "14:30"]]
    assert res["12"]["2026-11-27"] == [["00:00", "02:00"]]
    assert "bad" not in res
    assert sorted(res, key=power.queue_sort_key) == ["1", "12"]
    with pytest.raises(ValueError):
        power.parse_svitlyachok({"days": {}})
