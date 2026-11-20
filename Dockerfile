FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DB_PATH=/data/bot.db
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
RUN useradd -u 1000 -m app && mkdir /data && chown app /data
USER app
VOLUME /data

CMD ["python", "-m", "bot"]
