"""Массовая рассылка: несколько отправок параллельно, но не быстрее лимита Telegram.

Telegram пускает ~30 сообщений в секунду на бота (в разные чаты) и ~1 в секунду
в один чат. Раньше бот слал строго по одному и ждал ответа на каждое сообщение,
поэтому скорость упиралась в задержку сети (~50 мс → ~18 сообщений/с), а при
ответе 429 после двух попыток сообщение молча терялось.

Теперь: общий ограничитель скорости (по умолчанию 25/с, с запасом), до
SEND_CONCURRENCY отправок одновременно, при 429 вся рассылка ставится на паузу на
retry_after, скорость снижается, сообщение отправляется ещё раз (до
SEND_ATTEMPTS попыток).
Сообщения одному чату уходят по очереди, в исходном порядке.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable

log = logging.getLogger("bot.sender")


class RateLimiter:
    """Не чаще rate раз в секунду, ровным шагом.

    После 429 — общая пауза на retry_after и снижение скорости на 30% (если
    Telegram почему-то режет раньше 30/с, мы под него подстраиваемся, а не
    упираемся в паузы); после каждых 50 удачных отправок скорость растёт на 1/с,
    обратно до заданной.
    """

    def __init__(self, rate: float):
        self.max_rate = self.rate = rate
        self.next_at = 0.0
        self.pause_until = 0.0
        self.ok = 0

    async def wait(self):
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            t = max(self.next_at, self.pause_until)
            if t <= now:
                self.next_at = max(now, self.next_at) + 1 / self.rate
                return
            await asyncio.sleep(t - now)

    def success(self):
        self.ok += 1
        if self.ok >= 50 and self.rate < self.max_rate:
            self.ok = 0
            self.rate = min(self.max_rate, self.rate + 1)

    def pause(self, seconds: float):
        now = asyncio.get_running_loop().time()
        if now >= self.pause_until:   # одна волна 429 — одно снижение
            self.rate = max(1.0, self.rate * 0.7)
            self.ok = 0
            log.warning("Telegram 429: пауза %.0f с, скорость %.1f сообщ./с", seconds, self.rate)
        self.pause_until = max(self.pause_until, now + seconds)


async def broadcast(items: Iterable[tuple[int, str]],
                    send_one: Callable[[int, str], Awaitable[object]],
                    concurrency: int):
    """Отправляет [(chat_id, text), ...]. Одному чату — последовательно и по порядку."""
    per_chat: dict[int, list[str]] = {}
    for chat_id, text in items:
        per_chat.setdefault(chat_id, []).append(text)
    if not per_chat:
        return
    sem = asyncio.Semaphore(concurrency)

    async def one(chat_id: int, texts: list[str]):
        async with sem:
            for text in texts:
                await send_one(chat_id, text)

    t0 = asyncio.get_running_loop().time()
    await asyncio.gather(*(one(c, t) for c, t in per_chat.items()))
    n = sum(map(len, per_chat.values()))
    if n > 1:
        log.info("broadcast: %s messages to %s chats in %.1fs", n, len(per_chat),
                 asyncio.get_running_loop().time() - t0)
