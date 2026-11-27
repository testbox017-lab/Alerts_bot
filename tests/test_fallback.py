import asyncio
import importlib

import pytest

from bot import alerts, config

A = {"location_title": "Ніжинський район", "alert_type": "air_raid"}
CHAIN = ["alertsinua", "ukrainealarm", "mirror"]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "ALERTS_CHAIN", CHAIN)
    monkeypatch.setattr(alerts, "state", alerts._State())
    clock = {"t": 1000.0}
    monkeypatch.setattr(alerts.time, "monotonic", lambda: clock["t"])
    calls = []
    up = {s: True for s in CHAIN}

    async def fake(session, source):
        calls.append(source)
        if not up[source]:
            raise RuntimeError(f"{source} down")
        return {alerts.alert_key(A): A}

    monkeypatch.setattr(alerts, "_fetch_from", fake)
    return clock, calls, up


def run():
    return asyncio.run(alerts.fetch_active(None))


def fail_n(n):
    for _ in range(n):
        with pytest.raises(RuntimeError):
            run()


def test_chain_down_and_back(env):
    clock, calls, up = env
    assert run() and calls == ["alertsinua"]
    up["alertsinua"] = False
    fail_n(alerts.FAILS_TO_SWITCH - 1)
    calls.clear()
    assert run()                       # 3-я ошибка — сразу берём со следующего
    assert calls == ["alertsinua", "ukrainealarm"] and alerts.source_name() == "ukrainealarm.com"

    up["ukrainealarm"] = False         # падает и второй — уходим на зеркало
    fail_n(alerts.FAILS_TO_SWITCH - 1)
    calls.clear()
    assert run()
    assert calls == ["ukrainealarm", "mirror"] and "siren" in alerts.source_name()

    calls.clear()
    run()                              # пока не пришло время — приоритетные не трогаем
    assert calls == ["mirror"]

    up["ukrainealarm"] = True
    clock["t"] += alerts.RETRY_PRIMARY
    calls.clear()
    run()                              # alerts.in.ua ещё лежит, ukrainealarm ожил
    assert calls == ["alertsinua", "ukrainealarm"] and alerts.source_name() == "ukrainealarm.com"

    up["alertsinua"] = True
    calls.clear()
    run()                              # до следующей проверки остаёмся
    assert calls == ["ukrainealarm"]
    clock["t"] += alerts.RETRY_PRIMARY
    calls.clear()
    run()
    assert calls == ["alertsinua"] and alerts.source_name() == "alerts.in.ua"


def test_last_source_just_raises(env, monkeypatch):
    _, calls, up = env
    monkeypatch.setattr(config, "ALERTS_CHAIN", ["mirror"])
    up["mirror"] = False
    fail_n(alerts.FAILS_TO_SWITCH + 1)
    assert set(calls) == {"mirror"}


@pytest.mark.parametrize("env_vars,chain", [
    ({}, ["mirror"]),
    ({"ALERTS_TOKEN": "x"}, ["alertsinua", "mirror"]),
    ({"UKRAINEALARM_TOKEN": "y"}, ["ukrainealarm", "mirror"]),
    ({"ALERTS_TOKEN": "x", "UKRAINEALARM_TOKEN": "y"}, ["alertsinua", "ukrainealarm", "mirror"]),
    ({"ALERTS_TOKEN": "x", "UKRAINEALARM_TOKEN": "y", "ALERTS_SOURCE": "ukrainealarm"},
     ["ukrainealarm", "alertsinua", "mirror"]),
    # опечатка или источник без ключа в ALERTS_SOURCE — порядок по умолчанию
    ({"ALERTS_TOKEN": "x", "ALERTS_SOURCE": "ukrainealram"}, ["alertsinua", "mirror"]),
    ({"ALERTS_SOURCE": "alertsinua"}, ["mirror"]),
])
def test_chain_from_env(monkeypatch, env_vars, chain):
    for k in ("ALERTS_TOKEN", "UKRAINEALARM_TOKEN", "ALERTS_SOURCE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    try:
        assert importlib.reload(config).ALERTS_CHAIN == chain
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_key_same_across_sources():
    a = {"location_title": "Куп’янський район", "alert_type": "air_raid", "location_uid": "1"}
    b = {"location_title": "Куп'янський  район", "alert_type": "air_raid", "location_uid": "123"}
    assert alerts.alert_key(a) == alerts.alert_key(b)


def test_set_first_and_poll(monkeypatch):
    monkeypatch.setattr(config, "SOURCES", CHAIN)
    monkeypatch.setattr(config, "ALERTS_CHAIN", CHAIN)
    monkeypatch.setattr(config, "ALERTS_POLL_SECONDS", 15)
    monkeypatch.setattr(config, "UKRAINEALARM_POLL_SECONDS", 60)
    monkeypatch.setattr(alerts, "state", alerts._State())
    assert alerts.poll_seconds() == 15
    alerts.state.idx = 1               # alerts.in.ua упал, работаем с ukrainealarm
    assert alerts.poll_seconds() == 60
    alerts.set_first("ukrainealarm")
    assert config.ALERTS_CHAIN == ["ukrainealarm", "alertsinua", "mirror"]
    assert alerts.state.idx == 0 and alerts.poll_seconds() == 60
    alerts.set_first("mirror")
    assert config.ALERTS_CHAIN == ["mirror", "alertsinua", "ukrainealarm"]
    assert alerts.poll_seconds() == 15


def test_db_settings():
    from bot.db import DB
    db = DB(":memory:")
    assert db.setting("alerts_first") is None
    db.set_setting("alerts_first", "ukrainealarm")
    db.set_setting("alerts_first", "alertsinua")
    assert db.setting("alerts_first") == "alertsinua"
