from bot.alerts import matches
from bot.ukrainealarm import convert, parse_regions

TREE = {"states": [{
    "regionId": "25", "regionName": "Чернігівська область", "regionType": "State",
    "regionChildIds": [{
        "regionId": "140", "regionName": "Ніжинський район", "regionType": "District",
        "regionChildIds": [{"regionId": "1295", "regionName": "Бахмацька територіальна громада",
                            "regionType": "Community", "regionChildIds": []}]}]},
    {"regionId": "31", "regionName": "м. Київ", "regionType": "State", "regionChildIds": []}]}


def test_parse_regions():
    r = parse_regions(TREE)
    assert r["1295"] == {"name": "Бахмацька територіальна громада", "type": "hromada",
                         "oblast": "Чернігівська область", "raion": "Ніжинський район"}
    assert r["31"]["type"] == "oblast" and r["31"]["oblast"] == "м. Київ"


def test_convert_and_match():
    alerts = [
        {"regionId": "1295", "regionType": "Community", "regionName": "Бахмацька територіальна громада",
         "activeAlerts": [{"regionId": "1295", "regionType": "Community", "type": "AIR"}]},
        {"regionId": "140", "regionType": "District", "activeAlerts": [
            {"regionId": "140", "regionType": "District", "type": "ARTILLERY"}]},
        {"regionId": "31", "regionType": "State", "activeAlerts": [
            {"regionId": "31", "regionType": "State", "type": "AIR"}]},
    ]
    res = convert(alerts, parse_regions(TREE))
    assert set(res) == {"1295|air_raid", "140|artillery_shelling", "31|air_raid"}
    ob = "Чернігівська область"
    assert matches(res["1295|air_raid"], ob, "Бахмацька")
    assert not matches(res["140|artillery_shelling"], ob, "Бахмацька")
    assert matches(res["140|artillery_shelling"], ob, None)
    assert matches(res["31|air_raid"], "м. Київ", None)
    assert matches(res["31|air_raid"], "м.Київ", None)  # пробелы не важны
    assert not matches(res["31|air_raid"], ob, None)


def test_real_shapes_district_alert_under_community_and_yellow():
    tree = {"states": [
        {"regionId": "28", "regionName": "Донецька область", "regionType": "State", "regionChildIds": [
            {"regionId": "47", "regionName": "Покровський район", "regionType": "District", "regionChildIds": [
                {"regionId": "353", "regionName": "Покровська територіальна громада", "regionType": "Community"}]}]},
        {"regionId": "1293", "regionName": "м. Харків та Харківська територіальна громада", "regionType": "State"},
        {"regionId": "0", "regionName": "Тестовий регіон", "regionType": "State"}]}
    regions = parse_regions(tree)
    assert "0" not in regions
    assert regions["1293"]["oblast"] == "Харківська область" and regions["1293"]["type"] == "hromada"
    data = [{"regionId": "353", "regionType": "Community", "regionName": "Покровська територіальна громада",
             "activeAlerts": [
                 {"regionId": "353", "regionType": "Community", "type": "AIR",
                  "activeAlertLevels": [{"alertLevel": "Yellow"}]},
                 {"regionId": "47", "regionType": "District", "type": "AIR",
                  "activeAlertLevels": [{"alertLevel": "Red"}]}]},
            {"regionId": "1293", "regionType": "State", "activeAlerts": [
                {"regionId": "1293", "regionType": "State", "type": "AIR",
                 "activeAlertLevels": [{"alertLevel": "Red"}]}]}]
    res = convert(data, regions)
    assert set(res) == {"47|air_raid", "1293|air_raid"}
    assert res["47|air_raid"]["location_title"] == "Покровський район"
    assert matches(res["47|air_raid"], "Донецька область", "Покровськ")
    assert matches(res["1293|air_raid"], "Харківська область", None)
