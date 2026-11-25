"""Telegram-бот: воздушные тревоги + графики отключений света для многих чатов."""

import asyncio
import logging
import time
from datetime import datetime, timedelta

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

from . import alerts, compare, config, power
from .config import TZ
from .db import DB, Sub
from .regions import OBLASTS, POWER_SOURCES, sources_for_oblast

log = logging.getLogger("bot")
dp = Dispatcher()
db: DB
bot: Bot
http: aiohttp.ClientSession
# последняя загрузка каждого источника: {src: ({queue: {day: iv}}, update_str)}
source_cache: dict[str, tuple[dict, str]] = {}
# состояние источников графиков: последняя удачная загрузка, lastUpdated, текущая проблема
power_ok_at: dict[str, datetime] = {}
power_updated_at: dict[str, datetime | None] = {}
power_problem: dict[str, str | None] = {}
# официальные страницы графиков, на которые отправляем при проблемах с источником
POWER_SITES = {"chernihiv": "https://chernihivoblenergo.com.ua/blackouts"}

HELP = (
    "Я надсилаю повітряні тривоги по твоїх областях (до 5) або районах і нагадую про "
    "відключення світла за графіком твоєї черги.\n\n"
    "/start — налаштувати області і чергу\n"
    "/settings — поточні налаштування\n"
    "/status — активні тривоги зараз\n"
    "/raion Ніжинський, Бахмацька — тривоги лише області + цих районів/громад "
    "(/raion clear — вся область)\n"
    "/svet — графік на сьогодні й завтра\n"
    "/svet 06:00-09:00 15-18 — вказати графік вручну на сьогодні\n"
    "/svet zavtra 00-03 — вручну на завтра, /svet - — сьогодні без відключень\n"
    "/svet clear — прибрати ручні правки\n"
    "/remind 30 — за скільки хвилин попереджати (0 — вимкнути)\n"
    "/stop — відписатися\n\n"
    "У групі змінювати налаштування можуть лише адміни."
)


# ---------- утилиты ----------
def today() -> str:
    return datetime.now(TZ).date().isoformat()


def chat_title(m: Message) -> str:
    c = m.chat
    return c.title or c.full_name or str(c.id)


async def is_admin(chat_id: int, chat_type: str, user_id: int) -> bool:
    if chat_type == "private":
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest:
        return False
    return member.status in ("creator", "administrator")


async def reply(m: Message, text: str, reply_markup=None, **kw):
    """Ответ в чат. Без своей inline-клавиатуры прикладываем главное меню,
    чтобы нижние кнопки не пропадали (Telegram иногда их теряет: другое устройство,
    очистка истории, старое сообщение с меню удалено)."""
    return await m.answer(text, reply_markup=reply_markup or MENU, **kw)


async def send(chat_id: int, text: str):
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True,
                                   reply_markup=MENU)
            log.info("sent to %s: %s", chat_id, text[:80])
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            log.info("chat %s blocked/kicked the bot, removing", chat_id)
            db.delete(chat_id)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("send to %s failed: %s", chat_id, e)
            return


BTN_SVET = "💡 Графік світла"
BTN_STATUS = "🚨 Тривоги зараз"
BTN_SETTINGS = "⚙️ Налаштування"
BTN_HELP = "❓ Допомога"
MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_SVET), KeyboardButton(text=BTN_STATUS)],
              [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_HELP)]],
    resize_keyboard=True, is_persistent=True)
REMIND_CHOICES = [0, 10, 15, 30, 60]


def settings_kb() -> InlineKeyboardMarkup:
    return kb([[("📍 Область", "set:ob"), ("💡 Світло і черга", "set:pw")],
               [("⏰ Нагадування", "set:rm"), ("🔎 Райони", "set:rn")]])


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


def chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


MAX_OBLASTS = 5
OBLAST_PROMPT = (f"Обери області для тривог (можна до {MAX_OBLASTS}, напр. де живуть рідні) "
                 "і натисни «Готово»:")


def oblast_kb(selected: list[str]) -> InlineKeyboardMarkup:
    btns = [(("✅ " if o in selected else "") + o.replace(" область", ""), f"ob:{i}")
            for i, o in enumerate(OBLASTS)]
    rows = chunk(btns, 2)
    rows.append([("Готово ✔️", "obd")])
    return kb(rows)


def power_kb(oblasts: list[str], show_all: bool = False) -> InlineKeyboardMarkup:
    keys = list(POWER_SOURCES) if show_all else \
        [k for o in oblasts for k in sources_for_oblast(o)]
    rows = chunk([(POWER_SOURCES[k][0], f"pw:{k}") for k in keys], 2)
    if not show_all:
        rows.append([("Інший постачальник…", "pw:*")])
    rows.append([("Без графіка світла", "pw:-")])
    return kb(rows)


MAX_QUEUES = 5


def queue_kb(queues: list[str], selected: list[str]) -> InlineKeyboardMarkup:
    per_row = 6 if len(queues) > 20 else 4
    rows = chunk([(("✅ " if q in selected else "") + q, f"q:{q}") for q in queues], per_row)
    rows.append([("Готово ✔️", "qd")])
    return kb(rows)


def queue_label(sub: Sub) -> str:
    qs = sub.queues
    return ("черга " if len(qs) == 1 else "черги ") + (", ".join(qs) or "—")


def describe(sub: Sub) -> str:
    obs = sub.oblasts
    lines = [("📍 Область: " if len(obs) <= 1 else "📍 Області: ") + (", ".join(obs) or "не вибрано")]
    if sub.raion:
        lines.append(f"🔎 Фільтр районів: {sub.raion}")
    if sub.power:
        lines.append(f"💡 Світло: {POWER_SOURCES[sub.power][0]}, {queue_label(sub)}")
    else:
        lines.append("💡 Графік світла: вимкнено")
    lines.append(f"⏰ Нагадування за {sub.remind} хв" if sub.remind else "⏰ Нагадування вимкнено")
    return "\n".join(lines)


def svet_kb(sub: Sub) -> InlineKeyboardMarkup | None:
    return kb([[("📋 Всі черги", "all:0")]]) if sub.power else None


def all_queues_text(sub: Sub, offset: int) -> str:
    day = (datetime.now(TZ).date() + timedelta(days=offset)).isoformat()
    data = db.sched_day(sub.power, day)
    title = f"📋 {POWER_SOURCES[sub.power][0]}, {'сьогодні' if offset == 0 else 'завтра'} "
    if not data:
        return title + f"({power.fmt_day(day, [])[:5]}): графіка ще немає"
    lines = []
    for q in sorted(data, key=power.queue_sort_key):
        body = ", ".join(f"{a}–{b}" for a, b in data[q]) or "без відключень"
        mark = "👉 " if q in sub.queues else ""
        lines.append(f"{mark}{q}: {body}")
    return title + f"({power.fmt_day(day, [])[:5]})\n" + "\n".join(lines)


def schedule_text(sub: Sub) -> str:
    per_queue = db.schedules_for(sub, today())
    if not any(per_queue.values()):
        return "Графік на сьогодні/завтра ще невідомий."
    manual = db.overrides(sub.chat_id)

    def lines(data: dict) -> list[str]:
        return [power.fmt_day(d, iv) + (" ✍️" if d in manual else "") for d, iv in data.items()]

    if len(per_queue) == 1:
        return "💡 Відключення:\n" + "\n".join(lines(next(iter(per_queue.values()))))
    parts = [f"Черга {q}:\n" + ("\n".join(lines(data)) or "графіка ще немає")
             for q, data in per_queue.items()]
    return "💡 Відключення\n\n" + "\n\n".join(parts)


# ---------- источники графиков ----------
async def refresh_source(src: str) -> dict[str, list[str]]:
    """Загружает источник, сохраняет в БД. Возвращает {queue: [изменённые дни]}."""
    data, upd, updated_at = await power.fetch_source(http, POWER_SOURCES[src][1])
    source_cache[src] = (data, upd)
    power_ok_at[src] = datetime.now(TZ)
    power_updated_at[src] = updated_at
    first = not db.sched_has(src)
    t = today()
    changed: dict[str, list[str]] = {}
    for q, days in data.items():
        old = db.sched_get(src, q)
        for day, iv in days.items():
            if day >= t and old.get(day) != iv:
                db.sched_put(src, q, day, iv)
                if not first:
                    changed.setdefault(q, []).append(day)
    db.commit()
    return changed


async def check_power_health(src: str):
    """Один раз предупреждаем подписчиков источника о проблеме и один раз — когда прошла."""
    problem = power.health_problem(datetime.now(TZ), power_ok_at[src], power_updated_at.get(src))
    if problem == power_problem.get(src):
        return
    was = power_problem.get(src)
    power_problem[src] = problem
    log.warning("power source %s health: %s -> %s", src, was, problem)
    text = power.fmt_health(problem, POWER_SOURCES[src][0], power_updated_at.get(src),
                            POWER_SITES.get(src))
    for sub in db.all():
        if sub.power == src:
            await send(sub.chat_id, text)


async def power_loop():
    while True:
        db.cleanup((datetime.now(TZ).date() - timedelta(days=1)).isoformat())
        for src in db.used_sources():
            power_ok_at.setdefault(src, datetime.now(TZ))  # отсчёт «не грузится» — с запуска
            try:
                changed = await refresh_source(src)
            except Exception as e:  # noqa: BLE001
                log.warning("power source %s: %s", src, e)
                changed = {}
            await check_power_health(src)
            if not changed:
                continue
            upd = source_cache[src][1]
            for sub in db.all():
                mine = [q for q in sub.queues if q in changed] if sub.power == src else []
                if not mine:
                    continue
                manual = db.overrides(sub.chat_id)
                for q in mine:
                    sched = db.sched_get(src, q)
                    lines = [power.fmt_day(d, sched[d]) for d in sorted(changed[q])]
                    note = "\n(є ручна правка /svet — вона діє, поки не очистиш)" if \
                        any(d in manual for d in changed[q]) else ""
                    await send(sub.chat_id, f"📋 Графік черги {q} оновлено ({upd}):\n"
                               + "\n".join(lines) + note)
        await asyncio.sleep(config.POWER_POLL_SECONDS)


async def reminder_loop():
    while True:
        now = datetime.now(TZ)
        since = (now.date() - timedelta(days=1)).isoformat()
        for sub in db.all():
            if not sub.remind:
                continue
            per_queue = db.schedules_for(sub, since)
            many = len(per_queue) > 1
            for q, by_day in per_queue.items():
                for key, kind, t in power.due_reminders(by_day, now, sub.remind):
                    key = f"{q}|{key}" if many else key
                    if db.was_sent(sub.chat_id, key):
                        continue
                    left = max(0, round((t - now).total_seconds() / 60))
                    hhmm = f"{t:%H:%M}"
                    tag = f" (черга {q})" if many else ""
                    text = (f"🔌 Через {left} хв ({hhmm}) вимкнуть світло{tag}. Заряди павербанк!"
                            if kind == "off" else f"💡 Через {left} хв ({hhmm}) мають дати світло{tag}")
                    db.mark_sent(sub.chat_id, key, t.date().isoformat())
                    await send(sub.chat_id, text)
        await asyncio.sleep(30)


# ---------- тревоги ----------
# последний снимок тревог из alerts_loop: /status берёт его, а не делает
# лишний запрос (у ключа ukrainealarm лимит ~1 запрос в минуту)
last_alerts: dict[str, dict] | None = None
last_alerts_at = 0.0


async def fetch_alerts() -> dict[str, dict]:
    return await alerts.fetch_active(http)


async def current_alerts() -> dict[str, dict]:
    if last_alerts is not None and time.monotonic() - last_alerts_at < 3 * config.ALERTS_POLL_SECONDS:
        return last_alerts
    return await fetch_alerts()


async def alerts_loop():
    global last_alerts, last_alerts_at
    known: dict[str, dict] | None = None
    while True:
        try:
            current = await fetch_alerts()
            last_alerts, last_alerts_at = current, time.monotonic()
            if known is not None:
                new = [current[k] for k in current.keys() - known.keys()]
                gone = [known[k] for k in known.keys() - current.keys()]
                if new or gone:
                    log.info("alerts: +%s -%s", [a["location_title"] for a in new],
                             [a["location_title"] for a in gone])
                    for sub in db.all():
                        many = len(sub.oblasts) > 1
                        for a in new:
                            if alerts.matches(a, sub.oblast, sub.raion):
                                await send(sub.chat_id, alerts.fmt_alert(a, many))
                        for a in gone:
                            if alerts.matches(a, sub.oblast, sub.raion):
                                await send(sub.chat_id, alerts.fmt_clear(a, many))
            known = current
        except Exception as e:  # noqa: BLE001
            log.warning("alerts error: %s", alerts.err_text(e))
        await asyncio.sleep(config.ALERTS_POLL_SECONDS)


# ---------- команды ----------
async def guard(m: Message) -> bool:
    if await is_admin(m.chat.id, m.chat.type, m.from_user.id):
        return True
    await reply(m, "Змінювати налаштування в групі можуть лише адміни.")
    return False


@dp.message(CommandStart())
async def cmd_start(m: Message):
    if not await guard(m):
        return
    await reply(m, "Привіт! Кнопки меню — внизу екрана 👇", reply_markup=MENU)
    sub = db.ensure(m.chat.id, chat_title(m))
    await reply(m, OBLAST_PROMPT, reply_markup=oblast_kb(sub.oblasts))


@dp.message(Command("help"))
@dp.message(Command("menu"))
@dp.message(F.text == BTN_HELP)
async def cmd_help(m: Message):
    await reply(m, f"{HELP}\n\nДжерело тривог: {alerts.source_name()}", reply_markup=MENU)


@dp.message(Command("settings"))
@dp.message(F.text == BTN_SETTINGS)
async def cmd_settings(m: Message):
    sub = db.get(m.chat.id)
    if not sub:
        return await reply(m, "Ще не налаштовано. Натисни /start")
    await reply(m, describe(sub) + "\n\nЩо змінити?", reply_markup=settings_kb())


@dp.message(Command("stop"))
async def cmd_stop(m: Message):
    if not await guard(m):
        return
    db.delete(m.chat.id)
    await reply(m, "Відписано. Повернутися: /start")


@dp.message(Command("status"))
@dp.message(F.text == BTN_STATUS)
async def cmd_status(m: Message):
    sub = db.get(m.chat.id)
    if not sub or not sub.oblast:
        return await reply(m, "Спочатку обери область: /start")
    try:
        cur = await current_alerts()
    except Exception as e:  # noqa: BLE001
        return await reply(m, f"Помилка API тривог: {alerts.err_text(e)}")
    many = len(sub.oblasts) > 1
    mine = [alerts.fmt_alert(a, many) for a in cur.values()
            if alerts.matches(a, sub.oblast, sub.raion)]
    text = "\n".join(sorted(mine)) if mine else "🟢 Тривог немає"
    await reply(m, f"{text}\n\nДжерело: {alerts.source_name()}")


@dp.message(Command("compare"))
async def cmd_compare(m: Message):
    if not config.ALERTS_COMPARE:
        return await reply(m, "Порівняння джерел вимкнено (ALERTS_COMPARE=1 у .env).")
    await reply(m, compare.report_from_file())


@dp.message(Command("raion"))
async def cmd_raion(m: Message, command: CommandObject):
    sub = db.get(m.chat.id)
    if not sub:
        return await reply(m, "Спочатку /start")
    arg = (command.args or "").strip()
    if not arg:
        return await reply(m, f"Фільтр: {sub.raion or 'вся область'}\n"
                              "Приклад: /raion Ніжинський, Бахмацька")
    if not await guard(m):
        return
    db.update(m.chat.id, raion=None if arg.lower() in ("clear", "-") else arg)
    await reply(m, describe(db.get(m.chat.id)))


@dp.message(Command("remind"))
async def cmd_remind(m: Message, command: CommandObject):
    if not db.get(m.chat.id):
        return await reply(m, "Спочатку /start")
    if not await guard(m):
        return
    try:
        n = int((command.args or "").strip())
        assert 0 <= n <= 180
    except (ValueError, AssertionError):
        return await reply(m, "Вкажи хвилини від 0 до 180, напр. /remind 30")
    db.update(m.chat.id, remind=n)
    await reply(m, f"⏰ Нагадуватиму за {n} хв")


@dp.message(F.text == BTN_SVET)
async def btn_svet(m: Message):
    sub = db.get(m.chat.id)
    if not sub:
        return await reply(m, "Спочатку /start")
    await reply(m, schedule_text(sub), reply_markup=svet_kb(sub))


@dp.message(Command("svet"))
async def cmd_svet(m: Message, command: CommandObject):
    sub = db.get(m.chat.id)
    if not sub:
        return await reply(m, "Спочатку /start")
    args = (command.args or "").split()
    if not args:
        return await reply(m, schedule_text(sub), reply_markup=svet_kb(sub))
    if not await guard(m):
        return
    if args[0] == "clear":
        db.override_clear(m.chat.id)
        return await reply(m, "Ручні правки прибрано.\n" + schedule_text(sub))
    day = datetime.now(TZ).date()
    if args[0].lower() in ("zavtra", "завтра"):
        day += timedelta(days=1)
        args = args[1:]
    if args == ["-"]:
        intervals = []
    else:
        try:
            intervals = power.parse_intervals(args)
        except ValueError as e:
            return await reply(m, f"Не зрозумів інтервал: {e}\nПриклад: /svet 06:00-09:00 15-18")
    db.override_put(m.chat.id, day.isoformat(), intervals)
    await reply(m, "✍️ " + power.fmt_day(day.isoformat(), intervals))


# ---------- кнопки ----------
async def cb_guard(c: CallbackQuery) -> bool:
    if await is_admin(c.message.chat.id, c.message.chat.type, c.from_user.id):
        return True
    await c.answer("Лише адміни групи", show_alert=True)
    return False


@dp.callback_query(F.data.startswith("set:"))
async def cb_set(c: CallbackQuery):
    if not await cb_guard(c):
        return
    sub = db.get(c.message.chat.id)
    if c.data == "set:ob" or not sub:
        await c.message.edit_text(OBLAST_PROMPT, reply_markup=oblast_kb(sub.oblasts if sub else []))
    elif c.data == "set:rm":
        btns = [("вимкнути" if n == 0 else f"{n} хв", f"rm:{n}") for n in REMIND_CHOICES]
        await c.message.edit_text(f"Зараз: за {sub.remind} хв. За скільки попереджати "
                                  "про відключення і включення?", reply_markup=kb([btns]))
    elif c.data == "set:rn":
        await c.message.edit_text(
            f"🔎 Фільтр: {sub.raion or 'вся область'}\n\n"
            "Щоб отримувати тривоги лише на всю область + свої райони/громади, надішли:\n"
            "(при кількох областях фільтр діє на всі — перелічи райони кожної)\n"
            "/raion Ніжинський, Бахмацька",
            reply_markup=kb([[("Вся область", "rn:clear")]]))
    else:
        await c.message.edit_text("Чий графік відключень світла?", reply_markup=power_kb(sub.oblasts))
    await c.answer()


@dp.callback_query(F.data.startswith("all:"))
async def cb_all_queues(c: CallbackQuery):
    sub = db.get(c.message.chat.id)
    if not sub or not sub.power:
        return await c.answer("Спочатку обери постачальника в ⚙️ Налаштування", show_alert=True)
    offset = int(c.data[4:])
    other = ("➡️ Завтра", "all:1") if offset == 0 else ("⬅️ Сьогодні", "all:0")
    try:
        await c.message.edit_text(all_queues_text(sub, offset), reply_markup=kb([[other]]))
    except TelegramBadRequest:  # текст не изменился
        pass
    await c.answer()


@dp.callback_query(F.data.startswith("rm:"))
async def cb_remind(c: CallbackQuery):
    if not await cb_guard(c):
        return
    n = int(c.data[3:])
    db.update(c.message.chat.id, remind=n)
    await c.message.edit_text(describe(db.get(c.message.chat.id)), reply_markup=settings_kb())
    await c.answer("Без нагадувань" if n == 0 else f"Нагадуватиму за {n} хв")


@dp.callback_query(F.data == "rn:clear")
async def cb_raion_clear(c: CallbackQuery):
    if not await cb_guard(c):
        return
    db.update(c.message.chat.id, raion=None)
    await c.message.edit_text(describe(db.get(c.message.chat.id)), reply_markup=settings_kb())
    await c.answer()


@dp.callback_query(F.data.startswith("ob:"))
async def cb_oblast(c: CallbackQuery):
    if not await cb_guard(c):
        return
    oblast = OBLASTS[int(c.data[3:])]
    chat = c.message.chat
    sel = db.ensure(chat.id, chat.title or chat.full_name).oblasts
    if oblast in sel:
        sel.remove(oblast)
    elif len(sel) >= MAX_OBLASTS:
        return await c.answer(f"Не більше {MAX_OBLASTS} областей", show_alert=True)
    else:
        sel.append(oblast)
    db.update(chat.id, oblast=",".join(sel) or None, **({} if sel else {"raion": None}))
    await c.message.edit_reply_markup(reply_markup=oblast_kb(sel))
    await c.answer(("✅ " if oblast in sel else "Прибрано ") + oblast)


@dp.callback_query(F.data == "obd")
async def cb_oblast_done(c: CallbackQuery):
    if not await cb_guard(c):
        return
    sub = db.get(c.message.chat.id)
    if not sub or not sub.oblasts:
        return await c.answer("Познач хоча б одну область", show_alert=True)
    await c.message.edit_text(f"📍 {', '.join(sub.oblasts)}\n\nЧий графік відключень світла?",
                              reply_markup=power_kb(sub.oblasts))
    await c.answer()


@dp.callback_query(F.data.startswith("pw:"))
async def cb_power(c: CallbackQuery):
    if not await cb_guard(c):
        return
    chat_id = c.message.chat.id
    sub = db.get(chat_id) or db.ensure(chat_id, c.message.chat.title)
    key = c.data[3:]
    if key == "*":
        await c.message.edit_text("Обери постачальника:", reply_markup=power_kb(sub.oblasts, True))
        return await c.answer()
    if key == "-":
        db.update(chat_id, power=None, queue=None)
        await c.message.edit_text("Готово!\n\n" + describe(db.get(chat_id)))
        await reply(c.message, "Кнопки меню — внизу 👇", reply_markup=MENU)
        return await c.answer()
    await c.answer("Завантажую графік…")
    try:
        await refresh_source(key)
    except Exception as e:  # noqa: BLE001
        log.warning("source %s: %s", key, e)
    queues = sorted(source_cache.get(key, ({}, ""))[0], key=power.queue_sort_key)
    if not queues:
        return await c.message.edit_text("Не вдалося завантажити графік, спробуй пізніше: /settings")
    keep = [q for q in sub.queues if q in queues] if sub.power == key else []
    db.update(chat_id, power=key, queue=",".join(keep) or None)
    await c.message.edit_text(
        f"{POWER_SOURCES[key][0]}\nПозначь свою чергу (можна до {MAX_QUEUES}, напр. дім і дача) "
        "і натисни «Готово»:", reply_markup=queue_kb(queues, keep))


@dp.callback_query(F.data.startswith("q:"))
async def cb_queue(c: CallbackQuery):
    if not await cb_guard(c):
        return
    chat_id = c.message.chat.id
    sub = db.get(chat_id)
    if not sub or not sub.power:
        return await c.answer("Спочатку обери постачальника", show_alert=True)
    q = c.data[2:]
    sel = sub.queues
    if q in sel:
        sel.remove(q)
    elif len(sel) >= MAX_QUEUES:
        return await c.answer(f"Не більше {MAX_QUEUES} черг", show_alert=True)
    else:
        sel.append(q)
    sel.sort(key=power.queue_sort_key)
    db.update(chat_id, queue=",".join(sel) or None)
    if sub.power not in source_cache:
        try:
            await refresh_source(sub.power)
        except Exception as e:  # noqa: BLE001
            log.warning("source %s: %s", sub.power, e)
    queues = sorted(source_cache.get(sub.power, ({}, ""))[0], key=power.queue_sort_key) or sel
    await c.message.edit_reply_markup(reply_markup=queue_kb(queues, sel))
    await c.answer(("✅ " if q in sel else "Прибрано ") + q)


@dp.callback_query(F.data == "qd")
async def cb_queue_done(c: CallbackQuery):
    if not await cb_guard(c):
        return
    sub = db.get(c.message.chat.id)
    if not sub or not sub.queues:
        return await c.answer("Познач хоча б одну чергу", show_alert=True)
    await c.message.edit_text("Готово!\n\n" + describe(sub) + "\n\n" + schedule_text(sub),
                              reply_markup=svet_kb(sub))
    await reply(c.message, "Кнопки меню — внизу 👇", reply_markup=MENU)
    await c.answer()


@dp.message(F.chat.type == "private")
async def fallback(m: Message):
    # любое непонятное сообщение в личке возвращает кнопки меню
    await reply(m, "Кнопки меню — внизу 👇")


# ---------- запуск ----------
async def main():
    global db, bot, http
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("Задай BOT_TOKEN в .env")
    log.info("Источник тревог: %s (цепочка: %s)", alerts.source_name(),
             " → ".join(alerts.source_name(s) for s in config.ALERTS_CHAIN))
    db = DB()
    bot = Bot(config.BOT_TOKEN)
    http = aiohttp.ClientSession()
    await bot.set_my_commands([
        BotCommand(command="start", description="Налаштувати"),
        BotCommand(command="status", description="Тривоги зараз"),
        BotCommand(command="svet", description="Графік світла"),
        BotCommand(command="settings", description="Налаштування"),
        BotCommand(command="help", description="Довідка"),
    ])
    loops = [power_loop, reminder_loop, alerts_loop]
    tasks = [asyncio.create_task(f()) for f in loops]
    if config.ALERTS_COMPARE:
        tasks.append(asyncio.create_task(compare.compare_loop(http)))
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await http.close()
        await bot.session.close()
