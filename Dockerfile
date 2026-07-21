FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data dir for ChatStore SQLite; non-root bot user
# Entrypoint runs as root briefly to chown the named volume, then drops to bot.
RUN groupadd -r bot && useradd -r -g bot -d /app -s /bin/false bot && \
    mkdir -p /app/data && \
    chown -R bot:bot /app && \
    chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1

USER root
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "bot.py"]
