FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY waraq ./waraq
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8791 \
    WEB_PORT=8787

EXPOSE 8791
CMD ["python", "-m", "waraq"]
