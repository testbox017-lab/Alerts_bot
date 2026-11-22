import asyncio

import pytest

from bot import alerts, config

A = {"location_title": "Ніжинський район", "alert_type": "air_raid"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "ALERTS_SOURCE", "alertsinua")
    monkeypatch.setattr(alerts, "state", alerts._State())
    clock = {"t": 1000.0}
    monkeypatch.setattr(alerts.time, "monotonic", lambda: clock["t"])
    calls = []
    up = {"alertsinua": True, "mirror": True}

    async def fake(session, source):
        calls.append(source)
        if not up[source]:
            raise RuntimeError(f"{source} down")
        return {alerts.alert_key(A): A}

    monkeypatch.setattr(alerts, "_fetch_from", fake)
    return clock, calls, up


def run():
    return asyncio.run(alerts.fetch_active(None))


def test_switch_to_mirror_and_back(env):
    clock, calls, up = env
    assert run() and calls == ["alertsinua"]
    up["alertsinua"] = False
    for _ in range(alerts.FAILS_TO_SWITCH - 1):
        with pytest.raises(RuntimeError):
            run()
    assert alerts.state.active == "alertsinua"
    calls.clear()
    assert run()                       # 3-я ошибка — сразу берём с зеркала
    assert calls == ["alertsinua", "mirror"] and alerts.state.active == "mirror"
    assert "siren" in alerts.source_name()
    calls.clear()
    run()                              # пока не пришло время — основной не трогаем
    assert calls == ["mirror"]
    clock["t"] += alerts.RETRY_PRIMARY
    calls.clear()
    run()                              # основной всё ещё лежит
    assert calls == ["alertsinua", "mirror"] and alerts.state.active == "mirror"
    up["alertsinua"] = True
    clock["t"] += alerts.RETRY_PRIMARY
    calls.clear()
    run()
    assert calls == ["alertsinua"] and alerts.state.active == "alertsinua"
    assert alerts.source_name() == "alerts.in.ua"


def test_mirror_only_has_no_fallback(env, monkeypatch):
    _, calls, up = env
    monkeypatch.setattr(config, "ALERTS_SOURCE", "mirror")
    up["mirror"] = False
    with pytest.raises(RuntimeError):
        run()
    assert calls == ["mirror"]


def test_key_same_across_sources():
    a = {"location_title": "Куп’янський район", "alert_type": "air_raid", "location_uid": "1"}
    b = {"location_title": "Куп'янський  район", "alert_type": "air_raid", "location_uid": "123"}
    assert alerts.alert_key(a) == alerts.alert_key(b)
