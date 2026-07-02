# syntax=docker/dockerfile:1.6

ARG SELENIUM_VERSION=148.0-20260505
ARG UV_VERSION=0.11.19

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv


# The selenium image bundles Chrome + Xvfb + VNC + noVNC + supervisord, so the browser runs
# against a real (virtual) display and is viewable live over noVNC. The Chrome major version is
# pinned by this tag (see pennyspy/tests/test_chrome_version.py).
#
# All scrapers drive Chrome directly over the DevTools Protocol with zendriver (no chromedriver,
# no WebDriver layer -> none of the automation tells a bank WAF detects), launching the bundled
# Chrome binary on the Xvfb display.
FROM selenium/standalone-chrome:${SELENIUM_VERSION} AS runtime

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    PATH="/opt/venv/bin:${PATH}" \
    CHROME_BIN=/usr/bin/google-chrome \
    CHROME_USER_DATA_DIR=/home/seluser/chrome-user-data \
    PENNYSPY_PORT=5056 \
    PENNYSPY_LOG_DIR=/app/data/logs \
    PENNYSPY_SCREENSHOT_DIR=/app/data/screenshots \
    FRONTEND_DIR=/app/frontend \
    # Ship visible by default; set PENNYSPY_HEADLESS=true to run headless.
    PENNYSPY_HEADLESS=false \
    # Ensure the display + VNC + noVNC stack starts so the browser is watchable.
    SE_START_XVFB=true \
    SE_START_VNC=true \
    SE_START_NO_VNC=true

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Resolve dependencies first (cached) against a uv-managed Python, then install the project.
COPY pyproject.toml uv.lock README.md ./
RUN uv python install 3.11 \
    && uv sync --locked --no-install-project

COPY pennyspy ./pennyspy
RUN uv sync --locked --no-editable

COPY frontend ./frontend

# Run the API under the image's supervisord and drop the bundled Selenium Grid program (the
# scrapers launch Chrome themselves via zendriver/CDP, so the Grid is unused). The Grid block is
# the last section of selenium.conf, so deleting from it to EOF keeps xvfb/vnc/novnc intact.
COPY docker/supervisor/pennyspy.conf /etc/supervisor/conf.d/pennyspy.conf
RUN awk '/\[program:selenium-standalone\]/{exit} {print}' /etc/supervisor/conf.d/selenium.conf > /tmp/selenium.conf \
    && mv /tmp/selenium.conf /etc/supervisor/conf.d/selenium.conf \
    && mkdir -p "${CHROME_USER_DATA_DIR}" /app/data/logs /app/data/screenshots \
    && chown -R 1200:1201 "${CHROME_USER_DATA_DIR}" /app/data \
    && chmod 700 "${CHROME_USER_DATA_DIR}"

EXPOSE 5056 7900

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
port=os.environ.get('PENNYSPY_PORT','5056'); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/docs', timeout=3).status==200 else 1)"

# Back to the unprivileged selenium user; the base CMD (/opt/bin/entry_point.sh -> supervisord)
# is preserved so the display stack and the app start together.
USER 1200
