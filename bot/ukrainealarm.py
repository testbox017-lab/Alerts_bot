"""Тревоги из API приложения «Повітряна тривога» (api.ukrainealarm.com v3).

Официальный хост требует ключ (UKRAINEALARM_TOKEN). Без ключа используем
публичное зеркало того же API siren.pp.ua (его же использует интеграция
Home Assistant «Ukraine Alarm»). Ответ переводим в формат alerts.in.ua,
чтобы фильтр alerts.matches работал одинаково для всех источников.
"""

import time

import aiohttp

OFFICIAL_URL = "https://api.ukrainealarm.com/api/v3"
MIRROR_URL = "https://siren.pp.ua/api/v3"
REGIONS_TTL = 24 * 3600

TYPES = {"AIR": "air_raid", "ARTILLERY": "artillery_shelling", "URBAN_FIGHTS": "urban_fights",
         "CHEMICAL": "chemical", "NUCLEAR": "nuclear"}
LEVEL = {"State": "oblast", "District": "raion", "Community": "hromada"}
# В /regions некоторые громады-города лежат на верхнем уровне как "State"
# (проверено 2026-11-20). Привязываем их к настоящей области.
PSEUDO_STATES = {"564": "Запорізька область", "1293": "Харківська область"}
SKIP_IDS = {"0"}  # Тестовий регіон

_regions: dict[str, dict] = {}
_regions_at = 0.0


def parse_regions(tree: dict) -> dict[str, dict]:
    """/regions -> {regionId: {name, type, oblast, raion}}."""
    out: dict[str, dict] = {}

    def walk(node: dict, oblast: str | None, raion: str | None):
        rid = str(node.get("regionId"))
        rtype = node.get("regionType")
        name = node.get("regionName", "")
        if rid in PSEUDO_STATES:
            oblast, rtype = PSEUDO_STATES[rid], "Community"
        elif rtype == "State":
            oblast = name
        elif rtype == "District":
            raion = name
        out.setdefault(rid, {"name": name, "type": LEVEL.get(rtype, "hromada"),
                             "oblast": oblast, "raion": raion})
        for child in node.get("regionChildIds") or []:
            walk(child, oblast, raion)

    states = [s for s in tree.get("states", []) if str(s.get("regionId")) not in SKIP_IDS]
    # сначала настоящие области, потом «псевдо-области», чтобы не перезаписать громаду
    states.sort(key=lambda s: str(s.get("regionId")) in PSEUDO_STATES)
    for state in states:
        walk(state, None, None)
    return out


def convert(alerts_json: list, regions: dict[str, dict]) -> dict[str, dict]:
    """/alerts -> {ключ: alert в формате alerts.in.ua}."""
    res: dict[str, dict] = {}
    for entry in alerts_json or []:
        for a in entry.get("activeAlerts") or []:
            # alertLevel (Red/Yellow) не фильтруем: «Yellow» — тоже настоящая тревога,
            # regionHistory пишет её как AIR (проверено 2026-11-22, Ніжинський район).
            rid = str(a.get("regionId") or entry.get("regionId"))
            reg = regions.get(rid)
            if reg is None:
                if rid == str(entry.get("regionId")) and entry.get("regionName"):
                    reg = {"name": entry["regionName"], "type": LEVEL.get(entry.get("regionType"), "hromada"),
                           "oblast": None, "raion": None}
                else:
                    continue
            kind = TYPES.get(a.get("type"), (a.get("type") or "").lower())
            res[f"{rid}|{kind}"] = {
                "location_uid": rid,
                "location_title": reg["name"],
                "location_type": reg["type"],
                "location_oblast": reg["oblast"] or reg["name"],
                "location_raion": reg["raion"],
                "alert_type": kind,
            }
    return res


async def fetch_active(session: aiohttp.ClientSession, token: str) -> dict[str, dict]:
    global _regions, _regions_at
    base = OFFICIAL_URL if token else MIRROR_URL
    headers = {"Authorization": token} if token else {}
    timeout = aiohttp.ClientTimeout(total=15)
    if not _regions or time.monotonic() - _regions_at > REGIONS_TTL:
        async with session.get(f"{base}/regions", headers=headers, timeout=timeout) as r:
            r.raise_for_status()
            _regions = parse_regions(await r.json(content_type=None))
            _regions_at = time.monotonic()
    async with session.get(f"{base}/alerts", headers=headers, timeout=timeout) as r:
        r.raise_for_status()
        return convert(await r.json(content_type=None), _regions)
