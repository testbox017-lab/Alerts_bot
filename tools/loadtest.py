"""Нагрузочный тест рассылки без настоящего Telegram.

Поднимает локальный фейковый Bot API (задержка ответа, лимит ~30 сообщений/с
с ответом 429 retry_after, часть чатов «заблокировала бота» → 403), создаёт
N подписчиков во временной SQLite-базе и прогоняет настоящий код бота:
  * alerts  — одна новая тревога по области, которую ждут все N чатов;
  * remind  — напоминание «через N минут выключат свет» для всех N чатов.

Реальные пользователи и боевой бот не трогаются: токен фейковый, запросы идут
на 127.0.0.1.

    python -m tools.loadtest --users 1000 --latency 50
    python -m tools.loadtest --users 5000 --latency 30 --scenario remind
"""

import argparse
import asyncio
import json
import os
import resource
import sys
import tempfile
import time
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from bot import alerts, config, main
from bot.db import DB

OBLAST = "Чернігівська область"
ALERT = {"location_title": OBLAST, "location_oblast": OBLAST, "location_type": "oblast",
         "alert_type": "air_raid"}


class FakeTelegram:
    def __init__(self, latency_ms: float, limit: int, retry_after: int, blocked: set[int]):
        self.latency = latency_ms / 1000
        self.limit, self.retry_after, self.blocked = limit, retry_after, blocked
        self.window: list[float] = []
        self.delivered: dict[int, list[tuple[float, str]]] = {}
        self.n429 = self.n403 = 0
        self.inflight = self.max_inflight = 0

    async def handle(self, request: web.Request):
        method = request.match_info["method"]
        data = dict(await request.post()) if request.can_read_body else {}
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(self.latency)
            if method != "sendMessage":
                return web.json_response({"ok": True, "result": True})
            chat = int(data["chat_id"])
            now = time.monotonic()
            self.window = [t for t in self.window if now - t < 1.0]
            if len(self.window) >= self.limit:
                self.n429 += 1
                return web.json_response(
                    {"ok": False, "error_code": 429,
                     "description": f"Too Many Requests: retry after {self.retry_after}",
                     "parameters": {"retry_after": self.retry_after}}, status=429)
            self.window.append(now)
            if chat in self.blocked:
                self.n403 += 1
                return web.json_response({"ok": False, "error_code": 403,
                                          "description": "Forbidden: bot was blocked by the user"},
                                         status=403)
            self.delivered.setdefault(chat, []).append((now, data.get("text", "")))
            return web.json_response({"ok": True, "result": {
                "message_id": 1, "date": int(time.time()),
                "chat": {"id": chat, "type": "private"}, "text": data.get("text", "")}})
        finally:
            self.inflight -= 1


def make_db(path: str, users: int) -> DB:
    db = DB(path)
    rows = [(1_000_000 + i, f"user{i}", OBLAST, None, "chernihiv", "1.1", 30) for i in range(users)]
    db.c.executemany("INSERT INTO subs(chat_id,title,oblast,raion,power,queue,remind) "
                     "VALUES(?,?,?,?,?,?,?)", rows)
    db.commit()
    return db


async def wait_delivered(fake: FakeTelegram, want: int, timeout: float):
    t0 = time.monotonic()
    while sum(len(v) for v in fake.delivered.values()) + fake.n403 < want:
        if time.monotonic() - t0 > timeout:
            break
        await asyncio.sleep(0.05)
    # дать «застрявшим» отправкам завершиться / проявиться потерям
    await asyncio.sleep(0.5)


async def run(args) -> dict:
    blocked = {1_000_000 + i for i in range(0, args.users, max(1, int(100 / args.blocked)))} \
        if args.blocked else set()
    fake = FakeTelegram(args.latency, args.limit, args.retry_after, blocked)
    app = web.Application()
    app.router.add_post("/bot{token}/{method}", fake.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    tmp = tempfile.mkdtemp()
    main.db = make_db(os.path.join(tmp, "bot.db"), args.users)
    t_all = time.perf_counter()
    subs = main.db.all()
    t_all = time.perf_counter() - t_all
    main.bot = Bot("123:FAKE", session=AiohttpSession(
        api=TelegramAPIServer.from_base(f"http://127.0.0.1:{port}")))
    main.http = None

    # «пульс» event loop: насколько бот подвисает (команды пользователей тоже ждут)
    lag = {"max": 0.0}

    async def heartbeat():
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.05)
            lag["max"] = max(lag["max"], time.perf_counter() - t - 0.05)
    hb = asyncio.create_task(heartbeat())

    want = args.users
    polls = {"n": 0, "during": 0}
    if args.scenario == "alerts":
        async def fake_fetch(session):
            polls["n"] += 1
            if fake.delivered and sum(map(len, fake.delivered.values())) < want - len(blocked):
                polls["during"] += 1
            return {} if polls["n"] == 1 else {alerts.alert_key(ALERT): ALERT}
        alerts.fetch_active = fake_fetch
        alerts.poll_seconds = lambda: 1
        loop_coro = main.alerts_loop()
    else:
        now = datetime.now(config.TZ)
        start = now + timedelta(minutes=10)
        end = start + timedelta(hours=2)
        day = now.date().isoformat()
        if end.date() != now.date():  # не переходим через полночь — упрощение
            start, end = now.replace(hour=23, minute=0), now.replace(hour=23, minute=59)
        main.db.sched_put("chernihiv", "1.1", day, [[f"{start:%H:%M}", f"{end:%H:%M}"]])
        main.db.commit()
        loop_coro = main.reminder_loop()

    t0 = time.monotonic()
    task = asyncio.create_task(loop_coro)
    await wait_delivered(fake, want, args.timeout)
    task.cancel()
    hb.cancel()
    await asyncio.gather(task, hb, return_exceptions=True)
    # дождаться фоновых рассылок (если они есть)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    times = sorted(ts[0][0] - t0 for ts in fake.delivered.values())
    left = len(main.db.all())
    await main.bot.session.close()
    await runner.cleanup()

    def pct(p):
        return round(times[min(len(times) - 1, int(len(times) * p))], 1) if times else None
    dups = sum(1 for v in fake.delivered.values() if len(v) > 1)
    return {
        "scenario": args.scenario, "users": args.users, "latency_ms": args.latency,
        "tg_limit_per_s": args.limit, "blocked": len(blocked),
        "delivered": len(times), "lost": args.users - len(blocked) - len(times), "duplicates": dups,
        "first_s": pct(0), "median_s": pct(0.5), "p95_s": pct(0.95), "last_s": pct(1.0),
        "rate_msg_s": round(len(times) / times[-1], 1) if times and times[-1] else None,
        "http_429": fake.n429, "max_parallel": fake.max_inflight,
        "blocked_removed_from_db": args.users - left,
        "max_event_loop_freeze_s": round(lag["max"], 2),
        "alert_polls_while_sending": polls["during"] if args.scenario == "alerts" else None,
        "db_all_ms": round(t_all * 1000, 1),
        "rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=1000)
    p.add_argument("--latency", type=float, default=50, help="мс на ответ Telegram")
    p.add_argument("--limit", type=int, default=30, help="сообщений в секунду до 429")
    p.add_argument("--retry-after", type=int, default=3)
    p.add_argument("--blocked", type=float, default=2, help="%% чатов, заблокировавших бота")
    p.add_argument("--scenario", choices=["alerts", "remind"], default="alerts")
    p.add_argument("--timeout", type=float, default=900)
    args = p.parse_args()
    import logging
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))


if __name__ == "__main__":
    cli()
