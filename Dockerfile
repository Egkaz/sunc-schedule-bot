FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# шрифты для рендеринга карточек расписания (bot/render.py)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ bot/

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["python", "-m", "bot.bot"]
