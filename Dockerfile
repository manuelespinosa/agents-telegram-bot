FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No ejecutar como root
RUN groupadd -r bot && useradd -r -g bot -d /app -s /bin/false bot && \
    chown -R bot:bot /app
USER bot

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
