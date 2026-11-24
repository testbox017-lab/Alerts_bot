import json

from bot import compare


def test_first_source_and_delay():
    c = compare.Comparator()
    assert c.feed("alertsinua", "loc", set(), 0) == []
    assert c.feed("ukrainealarm", "loc", set(), 0) == []
    assert c.feed("alertsinua", "loc", {"x|air_raid"}, 15) == []
    assert c.feed("ukrainealarm", "loc", set(), 15) == []
    ev = c.feed("ukrainealarm", "loc", {"x|air_raid"}, 45)
    assert ev == [{"lvl": "loc", "kind": "start", "key": "x|air_raid",
                   "first": "alertsinua", "delay": 30, "t": 15}]
    # відбій: ukrainealarm раньше
    assert c.feed("ukrainealarm", "loc", set(), 100) == []
    ev = c.feed("alertsinua", "loc", set(), 115)
    assert ev[0]["kind"] == "end" and ev[0]["first"] == "ukrainealarm" and ev[0]["delay"] == 15


def test_only_one_source_expires():
    c = compare.Comparator()
    c.feed("alertsinua", "obl", set(), 0)
    c.feed("alertsinua", "obl", {"o|air_raid"}, 10)
    assert c.expire(100) == []
    ev = c.expire(10 + compare.MATCH_WINDOW + 1)
    assert ev[0]["only"] and ev[0]["first"] == "alertsinua"
    assert c.pending == {}


def test_report(monkeypatch):
    monkeypatch.setattr(compare.config, "ALERTS_COMPARE_SECONDS", 15)
    lines = [json.dumps(e) for e in [
        {"lvl": "obl", "kind": "start", "key": "a", "first": "alertsinua", "delay": 0.2, "at": "2026-11-24T12:00:00"},
        {"lvl": "obl", "kind": "start", "key": "b", "first": "ukrainealarm", "delay": 30, "at": "2026-11-24T12:05:00"},
        {"lvl": "obl", "kind": "start", "key": "c", "first": "ukrainealarm", "delay": 60, "at": "2026-11-24T12:06:00"},
        {"lvl": "loc", "kind": "end", "key": "d", "first": "alertsinua", "only": True, "at": "2026-11-24T12:07:00"},
    ]]
    r = compare.report(lines)
    assert "одночасно (різниця < інтервалу): 1" in r
    assert "ukrainealarm раніше: 2, медіана 45 с" in r
    assert "лише alerts.in.ua" in r
    assert compare.report([]).startswith("Даних ще немає")


def _resp_err(status, headers=None):
    from types import SimpleNamespace

    import aiohttp
    info = SimpleNamespace(real_url="https://api.ukrainealarm.com/api/v3/alerts",
                           headers={"Authorization": "SECRET"}, url="u", method="GET")
    return aiohttp.ClientResponseError(info, (), status=status, message="Too Many Requests",
                                       headers=headers)


def test_retry_after_and_no_token_in_log():
    from bot import alerts
    e = _resp_err(429, {"Retry-After": "45"})
    assert compare.retry_after(e) == 45
    assert compare.retry_after(_resp_err(429)) == 60
    assert compare.retry_after(_resp_err(500)) == 0
    assert compare.retry_after(RuntimeError("x")) == 0
    assert "SECRET" not in alerts.err_text(e)
    assert alerts.err_text(e) == "HTTP 429 Too Many Requests"
