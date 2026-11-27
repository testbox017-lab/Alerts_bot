import asyncio

from aiogram.exceptions import TelegramMigrateToChat, TelegramNetworkError

from bot import main
from bot.db import DB


class FakeBot:
    def __init__(self, errors):
        self.errors = list(errors)
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((chat_id, text))


def run_send(monkeypatch, errors, chat_id=-1):
    monkeypatch.setattr(main, "bot", FakeBot(errors), raising=False)
    monkeypatch.setattr(main, "db", DB(":memory:"), raising=False)
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    main.db.ensure(chat_id, "group")
    asyncio.run(main.send(chat_id, "🚨 тест"))
    return main.bot, main.db


async def _no_sleep(_):
    return None


def test_send_retries_network_error(monkeypatch):
    fake, _ = run_send(monkeypatch, [TelegramNetworkError(None, "timeout")])
    assert fake.sent == [(-1, "🚨 тест")]   # раньше тревога терялась после первой ошибки


def test_send_follows_supergroup_migration(monkeypatch):
    err = TelegramMigrateToChat(None, "migrated", migrate_to_chat_id=-100)
    fake, db = run_send(monkeypatch, [err])
    assert fake.sent == [(-100, "🚨 тест")]
    assert db.get(-1) is None and db.get(-100) is not None


def test_supervise_restarts_crashed_loop(monkeypatch):
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise KeyError("boom")

    asyncio.run(main.supervise(flaky))
    assert len(calls) == 3
