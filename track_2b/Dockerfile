# RedactGate + the Hack Apertus demo. Core install only (regex packs + Apertus adapter);
# no optional NER stack, no model weights in the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install .

COPY track_2b/src ./track_2b/src
COPY track_2b/data ./track_2b/data

CMD ["python", "track_2b/src/demo.py"]
