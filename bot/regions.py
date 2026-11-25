"""Справочники: области для тревог и источники графиков отключений."""

# location_title областей в alerts.in.ua
OBLASTS = [
    "Вінницька область", "Волинська область", "Дніпропетровська область",
    "Донецька область", "Житомирська область", "Закарпатська область",
    "Запорізька область", "Івано-Франківська область", "м. Київ",
    "Київська область", "Кіровоградська область", "Луганська область",
    "Львівська область", "Миколаївська область", "Одеська область",
    "Полтавська область", "Рівненська область", "Сумська область",
    "Тернопільська область", "Харківська область", "Херсонська область",
    "Хмельницька область", "Черкаська область", "Чернівецька область",
    "Чернігівська область", "Автономна Республіка Крим",
]

_OE = "https://raw.githubusercontent.com/yaroslav2901/OE_OUTAGE_DATA/main/data/"
_DTEK = "https://raw.githubusercontent.com/Baskerville42/outage-data-ua/main/data/"
_VOE = "https://raw.githubusercontent.com/vn-progr/gpv-voe-vinnytsia/main/data/"

# ключ -> (название для людей, URL JSON с графиком, область для подсказки)
# Вместо URL может быть "svitlo:<регион>" (svitlo.live) или "mykolaiv:" (API Миколаївобленерго).
POWER_SOURCES: dict[str, tuple[str, str, str]] = {
    "kyiv": ("Київ (ДТЕК)", _DTEK + "kyiv.json", "м. Київ"),
    "kyiv-region": ("Київщина (ДТЕК)", _DTEK + "kyiv-region.json", "Київська область"),
    "dnipro": ("Дніпропетровщина (ДТЕК)", _DTEK + "dnipro.json", "Дніпропетровська область"),
    "odesa": ("Одещина (ДТЕК)", _DTEK + "odesa.json", "Одеська область"),
    "cherkasy": ("Черкасиобленерго", _OE + "Cherkasyoblenergo.json", "Черкаська область"),
    "chernihiv": ("Чернігівобленерго", _OE + "Chernihivoblenergo.json", "Чернігівська область"),
    "kharkiv": ("Харківобленерго", _OE + "Kharkivoblenerho.json", "Харківська область"),
    "khmelnytskyi": ("Хмельницькобленерго", _OE + "Khmelnytskoblenerho.json", "Хмельницька область"),
    "lviv": ("Львівобленерго", _OE + "Lvivoblenerho.json", "Львівська область"),
    "poltava": ("Полтаваобленерго", _OE + "Poltavaoblenergo.json", "Полтавська область"),
    "if": ("Прикарпаттяобленерго", _OE + "Prykarpattiaoblenerho.json", "Івано-Франківська область"),
    "rivne": ("Рівнеобленерго", _OE + "Rivneoblenergo.json", "Рівненська область"),
    "ternopil": ("Тернопільобленерго", _OE + "Ternopiloblenerho.json", "Тернопільська область"),
    "zakarpattia": ("Закарпаттяобленерго", _OE + "Zakarpattiaoblenerho.json", "Закарпатська область"),
    "zaporizhzhia": ("Запоріжжяобленерго", _OE + "Zaporizhzhiaoblenergo.json", "Запорізька область"),
    "zhytomyr": ("Житомиробленерго", _OE + "Zhytomyroblenergo.json", "Житомирська область"),
    "vinnytsia": ("Вінницяобленерго", _VOE + "Vinnytsiaoblenerho.json", "Вінницька область"),
    "mykolaiv": ("Миколаївобленерго", "mykolaiv:", "Миколаївська область"),
}


def sources_for_oblast(oblast: str) -> list[str]:
    return [k for k, (_, _, ob) in POWER_SOURCES.items() if ob == oblast]
