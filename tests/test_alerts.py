from bot.alerts import fmt_alert, fmt_clear, matches

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


def test_matches_several_oblasts():
    obs = "Чернігівська область,м. Київ"
    assert matches(KYIV, obs, None) and matches(RAION, obs, None)
    assert not matches(KYIV, "Чернігівська область", None)
    # фильтр районов применяется к каждой области, тревога на всю область проходит всегда
    assert matches(KYIV, obs, "Ніжинський") and matches(RAION, obs, "Ніжинський")
    assert not matches(OTHER, obs, "Ніжинський")


def test_fmt_with_oblast():
    assert "(Чернігівська обл.)" in fmt_alert(RAION, True)
    assert "(" not in fmt_alert(RAION)
    assert fmt_clear(KYIV, True) == f'✅ Відбій: {KYIV["location_title"]}'


def test_sub_oblasts():
    from bot.db import DB
    d = DB(":memory:")
    d.ensure(1, "t")
    assert d.get(1).oblasts == []
    d.update(1, oblast="Чернігівська область,Київська область")
    assert d.get(1).oblasts == ["Чернігівська область", "Київська область"]


def test_fmt_list_grouped():
    from bot.alerts import fmt_list
    kyiv_obl = {"location_title": "Бучанський район", "location_type": "raion",
                "location_oblast": "Київська область", "location_raion": "Бучанський район",
                "alert_type": "air_raid"}
    items = [dict(a, alert_type="air_raid") for a in (OTHER, HROMADA, kyiv_obl, OBL, RAION)]
    one = fmt_list([a for a in items if a is not items[2]], ["Чернігівська область"])
    assert "📍" not in one and "(" not in one
    names = [line.split(": ", 1)[1] for line in one.splitlines()]
    # вся область, затем районы по алфавиту, громада сразу под своим районом
    assert names == ["Чернігівська область", "Корюківський район",
                     "Ніжинський район", "Бахмацька територіальна громада"]
    many = fmt_list(items, ["Київська область", "Чернігівська область"])
    blocks = many.split("\n\n")
    assert blocks[0].startswith("📍 Київська область") and "Бучанський" in blocks[0]
    assert blocks[1].startswith("📍 Чернігівська область") and "Бучанський" not in blocks[1]
