# Alerts_bot

Telegram-бот для себя, семьи или группы:

- 🚨 **воздушные тревоги** по выбранной области (можно сузить до районов/громад) — источник [alerts.in.ua](https://alerts.in.ua);
- 💡 **графики отключений света** по вашей очереди с напоминанием за N минут до отключения и включения. Графики берутся из открытых сборщиков [OE_OUTAGE_DATA](https://github.com/yaroslav2901/OE_OUTAGE_DATA) и [outage-data-ua](https://github.com/Baskerville42/outage-data-ua).

Каждый чат (личка или группа) настраивает свою область и очередь кнопками через `/start`.

## Поддерживаемые графики света

Київ, Київщина, Дніпропетровщина, Одещина (ДТЕК), Черкаська, Чернігівська, Харківська, Хмельницька, Львівська, Полтавська, Івано-Франківська, Рівненська, Тернопільська, Закарпатська, Запорізька, Житомирська области.

Для остальных областей тревоги работают, а график можно вводить вручную: `/svet 06:00-09:00 15-18`.

## Кнопки и команды

После `/start` внизу экрана появляется меню: **💡 Графік світла**, **🚨 Тривоги зараз**, **⚙️ Налаштування** (область, очередь, напоминания, районы — кнопками), **❓ Допомога**. Те же действия доступны командами:

| Команда | Что делает |
|---|---|
| `/start` | выбрать область и очередь |
| `/settings` | текущие настройки, кнопки «изменить» |
| `/status` | активные тревоги сейчас |
| `/raion Ніжинський, Бахмацька` | тревоги только на всю область + эти районы/громады; `/raion clear` — вся область |
| `/svet` | график на сегодня и завтра |
| `/svet 06:00-09:00 15-18` | ручная правка на сегодня (`/svet zavtra …` — на завтра, `/svet -` — без отключений, `/svet clear` — убрать правки) |
| `/remind 30` | за сколько минут предупреждать (`0` — выключить) |
| `/stop` | отписаться |

В группах настройки меняют только админы. Команды бот видит всегда, а чтобы работали кнопки меню, сделайте бота админом группы (или выключите ему privacy mode в @BotFather → `/setprivacy` → Disable).

## Запуск на сервере (Ubuntu + Docker)

Один раз на сервере:

```bash
# Docker, если ещё нет
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # перелогиниться после этого

mkdir -p ~/alerts_bot/data && cd ~/alerts_bot
nano .env   # вставить содержимое .env.example и заполнить токены
```

- `BOT_TOKEN` — у [@BotFather](https://t.me/BotFather) (`/newbot`);
- `ALERTS_TOKEN` — запросить на [devs.alerts.in.ua](https://devs.alerts.in.ua). Без него бот тоже запустится, но только с графиками света; после добавления токена выполните `cd ~/alerts_bot && docker compose up -d --force-recreate`.

Дальше всё делает GitHub Actions.

## Автодеплой

При каждом push в `main` workflow `.github/workflows/deploy.yml`:

1. гоняет тесты;
2. собирает Docker-образ и публикует его в `ghcr.io/testbox017-lab/alerts_bot`;
3. по SSH копирует `docker-compose.yml` в `~/alerts_bot` на сервер и делает `docker compose pull && docker compose up -d`.

Нужные секреты (GitHub → репозиторий → Settings → Secrets and variables → Actions → New repository secret):

| Секрет | Значение |
|---|---|
| `SSH_HOST` | IP или домен сервера |
| `SSH_USER` | пользователь на сервере (в группе `docker`) |
| `SSH_KEY` | приватный SSH-ключ (весь текст, вместе с `-----BEGIN…`) |
| `SSH_PORT` | необязательно, по умолчанию 22 |

Отдельный ключ для деплоя:

```bash
# на своём компьютере
ssh-keygen -t ed25519 -f deploy_key -N "" -C "github-deploy"
ssh-copy-id -i deploy_key.pub user@server
# содержимое deploy_key -> секрет SSH_KEY
```

Пока секреты не заданы, деплой просто пропускается с предупреждением.

## Ручной запуск / разработка

```bash
cp .env.example .env   # заполнить
docker compose up -d --build
docker compose logs -f
```

Без Docker: `pip install -r requirements-dev.txt && python -m pytest && python -m bot`.

Данные (подписчики, кэш графиков) лежат в SQLite `data/bot.db`.
