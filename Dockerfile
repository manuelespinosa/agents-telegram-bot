FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data dir for ChatStore SQLite; non-root bot user
RUN groupadd -r bot && useradd -r -g bot -d /app -s /bin/false bot && \
    mkdir -p /app/data && \
    chown -R bot:bot /app
USER bot

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
