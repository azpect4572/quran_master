FROM python:3.13-slim

# librosa/pydub need ffmpeg for webm->wav conversion (scoring.py:77-90)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects PORT; Flask/SQLAlchemy SQLite lives in instance/ (ephemeral - resets on revision restart)
ENV PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 app:app
