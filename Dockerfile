# syntax=docker/dockerfile:1.6

ARG PYTHON_VERSION=3.11-slim-bookworm
ARG CHROME_VERSION=138.0.7204.94

FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

COPY pyproject.toml README.md ./
RUN mkdir -p pennyspy && echo "" > pennyspy/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf pennyspy

COPY pennyspy ./pennyspy
RUN pip install --no-cache-dir --no-deps .


FROM python:${PYTHON_VERSION} AS runtime

ARG CHROME_VERSION
ARG CHROME_LINK=https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    CHROME_USER_DATA_DIR=/home/pennyspy/chrome-user-data \
    PENNYSPY_PORT=5056 \
    PENNYSPY_LOG_DIR=/app/data/logs

# Runtime libs Chrome needs + minimal tooling for the install step.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
        libnspr4 \
        libnss3 \
        libxss1 \
        libasound2 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxcomposite1 \
        libxrandr2 \
        libgbm1 \
        libxdamage1 \
        libgtk-3-0 \
        fonts-liberation \
    && curl -fsSL "${CHROME_LINK}/chromedriver-linux64.zip" -o /tmp/chromedriver.zip \
    && unzip -q /tmp/chromedriver.zip -d /tmp \
    && mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && curl -fsSL "${CHROME_LINK}/chrome-linux64.zip" -o /tmp/chrome.zip \
    && unzip -q /tmp/chrome.zip -d /tmp/chrome-unpack \
    && mkdir -p /opt/google \
    && mv /tmp/chrome-unpack/chrome-linux64 /opt/google/chrome \
    && ln -sf /opt/google/chrome/chrome /usr/bin/google-chrome \
    && google-chrome --version \
    && chromedriver --version \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /tmp/chromedriver.zip /tmp/chrome.zip /tmp/chrome-unpack /tmp/chromedriver-linux64 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user to run the app (Chrome also refuses --no-sandbox-free runs as root).
RUN groupadd --system --gid 1000 pennyspy \
    && useradd --system --uid 1000 --gid pennyspy --create-home --shell /usr/sbin/nologin pennyspy \
    && mkdir -p "${CHROME_USER_DATA_DIR}" \
    && chown -R pennyspy:pennyspy "${CHROME_USER_DATA_DIR}" \
    && chmod 700 "${CHROME_USER_DATA_DIR}" \
    && mkdir -p /app/data/logs \
    && chown -R pennyspy:pennyspy /app/data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

ENV FRONTEND_DIR=/app/frontend

COPY frontend ./frontend

USER pennyspy

EXPOSE 5056

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
port=os.environ.get('PENNYSPY_PORT','5056'); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/docs', timeout=3).status==200 else 1)"

ENTRYPOINT ["pennyspy_api"]
