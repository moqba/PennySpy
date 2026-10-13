# syntax=docker/dockerfile:1.6

ARG SELENIUM_VERSION=153.0-20260909
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

# Run as uid/gid 1000 instead of the base image's 1200/1201. On most hosts 1000 is the first
# user, so a bind-mounted ./pennyspy-data created by that user is writable without a chown —
# and saved logins (pennyspy/credentials) must land on the volume, not in the container.
# seluser keeps its name; only the ids change. The base image's "ubuntu" account already holds
# 1000 and is unused, so it goes first. Everything the base image gave to 1200/1201
# (/home/seluser, /opt/bin, supervisor's run and log dirs, /etc/passwd) follows seluser.
ARG PENNYSPY_UID=1000
ARG PENNYSPY_GID=1000
RUN OLD_UID="$(id -u seluser)" OLD_GID="$(id -g seluser)" \
    && if getent passwd ubuntu >/dev/null; then userdel --remove ubuntu 2>/dev/null || userdel ubuntu; fi \
    && if getent group ubuntu >/dev/null; then groupdel ubuntu; fi \
    && groupmod --gid "${PENNYSPY_GID}" seluser \
    && usermod --uid "${PENNYSPY_UID}" --gid "${PENNYSPY_GID}" seluser \
    && find / -xdev \( -uid "${OLD_UID}" -o -gid "${OLD_GID}" \) -not -path '/proc/*' \
         -exec chown --no-dereference "${PENNYSPY_UID}:${PENNYSPY_GID}" {} +

ENV SEL_UID=${PENNYSPY_UID} \
    SEL_GID=${PENNYSPY_GID}

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
    && chown -R seluser:seluser "${CHROME_USER_DATA_DIR}" /app/data \
    && chmod 700 "${CHROME_USER_DATA_DIR}"

EXPOSE 5056 7900

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
port=os.environ.get('PENNYSPY_PORT','5056'); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/docs', timeout=3).status==200 else 1)"

# Back to the unprivileged selenium user; the base CMD (/opt/bin/entry_point.sh -> supervisord)
# is preserved so the display stack and the app start together. Referencing the account by name
# (rather than a hardcoded uid) keeps ownership aligned with whatever uid/gid the base image
# assigns seluser (1000 after the remap above), so the per-session Chrome user-data dir stays
# writable.
USER seluser
