# Nawaab Wk Bot
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update -y && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        gcc \
        libjpeg-dev \
        zlib1g-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Run gunicorn (web server for Render port detection) + bot in parallel
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} app:app & python3 main.py"]
