# RedactGate API image. Core install is regex-only (no heavy NER) so it builds fast and
# small; add the `ner` extra at build time with --build-arg INSTALL_NER=1 for Presidio.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_NER=0
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip \
    && if [ "$INSTALL_NER" = "1" ]; then pip install ".[ner]"; else pip install "."; fi \
    && if [ "$INSTALL_NER" = "1" ]; then python -m spacy download en_core_web_lg || true; fi

COPY migrations ./migrations

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000", \
     "--timeout", "180"]
