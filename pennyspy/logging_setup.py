import logging
import os
import pathlib
import sys
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
_DOCKER_DATA_DIR = pathlib.Path("/app/data")
_DOCKER_LOG_DIR = _DOCKER_DATA_DIR / "logs"

_configured = False
_configured_log_file: pathlib.Path | None = None


def _repo_checkout_log_dir() -> pathlib.Path | None:
    pkg_parent = pathlib.Path(__file__).resolve().parent.parent
    if (pkg_parent / "pyproject.toml").is_file():
        return pkg_parent / "pennyspy-data" / "logs"
    return None


def _home_log_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".pennyspy" / "logs"


def _resolve_log_dir() -> pathlib.Path:
    env_dir = os.getenv("PENNYSPY_LOG_DIR")
    if env_dir:
        return pathlib.Path(env_dir)
    if _DOCKER_DATA_DIR.exists() and os.access(_DOCKER_DATA_DIR, os.W_OK):
        return _DOCKER_LOG_DIR
    checkout_dir = _repo_checkout_log_dir()
    if checkout_dir is not None:
        return checkout_dir
    return _home_log_dir()


def _ensure_log_dir(preferred: pathlib.Path) -> pathlib.Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except PermissionError as exc:
        fallback = _home_log_dir()
        print(
            f"pennyspy: cannot write logs to {preferred} ({exc}); "
            f"falling back to {fallback}",
            file=sys.stderr,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _create_file_handler(log_dir: pathlib.Path, level: int) -> tuple[logging.Handler, pathlib.Path]:
    log_file = log_dir / "pennyspy.log"
    try:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(level)
        return handler, log_file
    except PermissionError as exc:
        fallback = _ensure_log_dir(_home_log_dir())
        fallback_log_file = fallback / "pennyspy.log"
        print(
            f"pennyspy: cannot write logs to {log_file} ({exc}); "
            f"falling back to {fallback_log_file}",
            file=sys.stderr,
        )
        handler = RotatingFileHandler(
            fallback_log_file,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(level)
        return handler, fallback_log_file


def _resolve_level(level: int | None) -> int:
    """Level from the caller, else PENNYSPY_LOG_LEVEL, else INFO.

    Reading the env var means a run can be turned up to DEBUG from docker-compose.yml
    without rebuilding the image.
    """
    if level is not None:
        return level
    name = os.getenv("PENNYSPY_LOG_LEVEL", "").strip().upper()
    if not name:
        return logging.INFO
    resolved = logging.getLevelName(name)
    if isinstance(resolved, int):
        return resolved
    print(f"pennyspy: unknown PENNYSPY_LOG_LEVEL {name!r}; using INFO", file=sys.stderr)
    return logging.INFO


def setup_logging(level: int | None = None) -> pathlib.Path:
    global _configured, _configured_log_file
    level = _resolve_level(level)
    log_dir = _ensure_log_dir(_resolve_log_dir())
    if _configured:
        if _configured_log_file is not None:
            return _configured_log_file
        return log_dir / "pennyspy.log"

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler, log_file = _create_file_handler(log_dir, level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    _configured = True
    _configured_log_file = log_file
    return log_file
