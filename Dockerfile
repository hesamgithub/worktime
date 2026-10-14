FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKTIME_DATABASE_URL=sqlite:////data/worktime.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The database lives in /data, which is meant to be a volume. SQLite keeps
# its write-ahead log next to the database file, so the whole directory is
# mounted rather than the single file.
RUN useradd --system --create-home worktime \
    && mkdir /data \
    && chown worktime:worktime /data
USER worktime
VOLUME /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "worktime.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
