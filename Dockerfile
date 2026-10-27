# Image für Bot und Dashboard (AP 8b). Beide laufen aus demselben Image, die docker-compose.yml
# wählt pro Dienst den Befehl. Die Pipeline baut es und schiebt es nach ghcr.io.
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/NicolasHunger/bollinger-bot"

# Logs sofort ausgeben, keine .pyc-Dateien, kein pip-Cache im Image. Streamlit ohne Rückfrage
# beim Start und ohne Nutzungsstatistik.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Nur die Laufzeit-Abhängigkeiten (pip install ., nicht .[dev]): Test-Werkzeuge gehören nicht ins
# Image. --only-binary :all: wie in der Pipeline: keine Setup-Skripte aus Quellpaketen.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --only-binary :all: .

# Nicht als root laufen. Dem Benutzer bot gehört nur /data, dort liegt die SQLite-Datei.
RUN useradd --create-home --uid 1000 bot && mkdir /data && chown bot:bot /data
USER bot

# Ohne Befehl startet das Dashboard. Den Bot startet die docker-compose.yml.
EXPOSE 8501
CMD ["streamlit", "run", "src/bollinger_bot/dashboard.py", "--server.address=0.0.0.0", "--server.port=8501"]
