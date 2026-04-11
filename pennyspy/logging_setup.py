import logging
import os
import pathlib
import sys
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
_DOCKER_DATA_DIR = pathlib.Path("/app/data")
_DOCKER_LOG_DIR = _DOCKER_DATA_DIR / "logs"

_configured = False


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


def setup_logging(level: int = logging.INFO) -> pathlib.Path:
    global _configured
    log_dir = _ensure_log_dir(_resolve_log_dir())
    log_file = log_dir / "pennyspy.log"
    if _configured:
        return log_file

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

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
    return log_file
