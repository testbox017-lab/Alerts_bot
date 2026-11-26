import asyncio

from bot import sender


def test_broadcast_keeps_order_per_chat_and_limits_parallelism():
    got: list[tuple[int, str]] = []
    active = {"now": 0, "max": 0}

    async def send_one(chat, text):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.001)
        got.append((chat, text))
        active["now"] -= 1

    items = [(c, f"{c}-{i}") for i in range(3) for c in range(50)]
    asyncio.run(sender.broadcast(items, send_one, concurrency=5))
    assert sorted(got) == sorted(items)
    for c in range(50):
        assert [t for ch, t in got if ch == c] == [f"{c}-0", f"{c}-1", f"{c}-2"]
    assert active["max"] == 5


def test_rate_limiter_spacing_and_pause():
    async def run():
        lim = sender.RateLimiter(100)          # 10 мс между отправками
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for _ in range(11):
            await lim.wait()
        spent = loop.time() - t0
        lim.pause(0.1)
        t1 = loop.time()
        await lim.wait()
        return spent, loop.time() - t1, lim.rate

    spent, paused, rate = asyncio.run(run())
    assert 0.09 <= spent < 0.3
    assert paused >= 0.09
    assert rate == 70                          # после 429 — минус 30%


def test_rate_recovers_after_successes():
    async def run():
        lim = sender.RateLimiter(10)
        lim.pause(0)
        lim.pause(0)                           # вторая волна 429 тоже снижает
        low = lim.rate
        for _ in range(50 * 10):
            lim.success()
        return low, lim.rate
    low, back = asyncio.run(run())
    assert low < 10 and back == 10
