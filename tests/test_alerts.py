from bot.alerts import matches

OBL = {"location_title": "Чернігівська область", "location_type": "oblast",
       "location_oblast": "Чернігівська область", "alert_type": "air_raid"}
RAION = {"location_title": "Ніжинський район", "location_type": "raion",
         "location_oblast": "Чернігівська область", "location_raion": "Ніжинський район"}
HROMADA = {"location_title": "Бахмацька територіальна громада", "location_type": "hromada",
           "location_oblast": "Чернігівська область", "location_raion": "Ніжинський район"}
OTHER = {"location_title": "Корюківський район", "location_type": "raion",
         "location_oblast": "Чернігівська область", "location_raion": "Корюківський район"}
KYIV = {"location_title": "м. Київ", "location_type": "city", "location_oblast": "м. Київ"}


def test_whole_oblast():
    ob = "Чернігівська область"
    assert all(matches(a, ob, None) for a in (OBL, RAION, HROMADA, OTHER))
    assert not matches(KYIV, ob, None)
    assert not matches(OBL, None, None)


def test_raion_filter():
    ob = "Чернігівська область"
    assert matches(OBL, ob, "Бахмацька")
    assert matches(HROMADA, ob, "Бахмацька")
    assert not matches(RAION, ob, "Бахмацька")
    assert not matches(OTHER, ob, "ніжинський, бахмацька")
    assert matches(RAION, ob, "ніжинський, бахмацька")


def test_kyiv_city():
    assert matches(KYIV, "м. Київ", None)
