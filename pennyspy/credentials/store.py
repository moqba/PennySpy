"""Bank logins saved from the web UI, encrypted at rest and never handed back out.

Each login is a username and password for one bank, plus a label the user picks to tell
several logins for the same bank apart. Both secrets are encrypted with Fernet
(AES-128-CBC + HMAC-SHA256) before they touch the disk; only the label, the bank and the
timestamps are stored in the clear. The key is generated on first save and kept in
``secret.key`` beside the store, readable by the owner only.

The store is write-only by design: :meth:`CredentialStore.list_logins` returns metadata and a
partly hidden username, and the only way to get a decrypted value is
:meth:`CredentialStore.resolve`, which the login flow calls to fill the bank's form. No API
endpoint wraps ``resolve``.

What this protects against: logins leaking through API responses, the web UI, logs, or a
copy of ``credentials.json`` alone. What it does not: anyone holding the whole data
directory has the key and the ciphertext together.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path
from typing import Any, Final, Literal, get_args

from cryptography.fernet import Fernet, InvalidToken

from pennyspy.scrapers.get_required_env_var import SecretString

logger = getLogger(__name__)

Bank = Literal["nbc", "ws"]
BANKS: Final[tuple[str, ...]] = get_args(Bank)

CREDENTIALS_FILENAME: Final[str] = "credentials.json"
KEY_FILENAME: Final[str] = "secret.key"
_CREDENTIALS_DIR_ENV: Final[str] = "PENNYSPY_CREDENTIALS_DIR"
_DOCKER_DATA_DIR: Final[Path] = Path("/app/data")
_STORE_VERSION: Final[int] = 1


class CredentialStoreError(RuntimeError):
    """The store cannot be read or written; the message says what to fix."""


class CredentialNotFound(KeyError):
    """No saved login has the requested id."""


@dataclass(frozen=True)
class CredentialInfo:
    """Everything about a saved login that may leave the server."""

    id: str
    bank: str
    label: str
    username_hint: str
    created_at: str
    updated_at: str


def resolve_credentials_dir() -> Path:
    """Where ``credentials.json`` and ``secret.key`` live.

    Unlike logs and screenshots, the store must not fall back to ``~/.pennyspy`` inside the
    container: that path is not on the volume, so every image update would silently wipe
    the saved logins. When ``/app/data`` exists it is used, writable or not, and a write
    that fails there says so.
    """
    env_dir = os.environ.get(_CREDENTIALS_DIR_ENV)
    if env_dir:
        return Path(env_dir)
    if _DOCKER_DATA_DIR.exists():
        return _DOCKER_DATA_DIR
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "pennyspy-data"
    return Path.home() / ".pennyspy"


def mask_username(username: str) -> str:
    """A hint that tells logins apart without revealing them.

    ``john.doe@gmail.com`` -> ``jo•••@gmail.com``, ``12345678`` -> ``12•••78``, and anything
    of six characters or fewer -> ``•••``.
    """
    local, at, domain = username.partition("@")
    if at:
        return f"{local[:2]}•••@{domain}" if len(local) > 2 else f"•••@{domain}"
    if len(username) <= 6:
        return "•••"
    return f"{username[:2]}•••{username[-2:]}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CredentialStore:
    """The saved logins in one directory. Safe to share across request threads."""

    def __init__(self, directory: Path | None = None):
        self._directory = directory
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else resolve_credentials_dir()

    @property
    def _store_path(self) -> Path:
        return self.directory / CREDENTIALS_FILENAME

    @property
    def _key_path(self) -> Path:
        return self.directory / KEY_FILENAME

    # ── Public API ────────────────────────────────────────────────────

    def list_logins(self, bank: str | None = None) -> list[CredentialInfo]:
        with self._lock:
            records = self._read_records()
            if not records:
                return []
            fernet = self._fernet(create=False)
            return [self._info(record, fernet) for record in records if bank is None or record["bank"] == bank]

    def get(self, credential_id: str) -> CredentialInfo:
        with self._lock:
            record = self._find(self._read_records(), credential_id)
            return self._info(record, self._fernet(create=False))

    def create(self, *, bank: str, label: str, username: str, password: str) -> CredentialInfo:
        _check_bank(bank)
        label, username = label.strip(), username.strip()
        if not label or not username or not password:
            raise ValueError("Label, username and password are all required")
        with self._lock:
            records = self._read_records()
            fernet = self._fernet(create=True)
            now = _now()
            record = {
                "id": str(uuid.uuid4()),
                "bank": bank,
                "label": label,
                "username_enc": _encrypt(fernet, username),
                "password_enc": _encrypt(fernet, password),
                "created_at": now,
                "updated_at": now,
            }
            records.append(record)
            self._write_records(records)
            logger.info("Saved new %s login %s (%s)", bank, record["id"], label)
            return self._info(record, fernet)

    def update(
        self,
        credential_id: str,
        *,
        label: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> CredentialInfo:
        """Change the given fields; ``None`` or blank keeps the current value."""
        with self._lock:
            records = self._read_records()
            record = self._find(records, credential_id)
            fernet = self._fernet(create=False)
            if label and label.strip():
                record["label"] = label.strip()
            if username and username.strip():
                record["username_enc"] = _encrypt(fernet, username.strip())
            if password:
                record["password_enc"] = _encrypt(fernet, password)
            record["updated_at"] = _now()
            self._write_records(records)
            logger.info("Updated %s login %s", record["bank"], credential_id)
            return self._info(record, fernet)

    def delete(self, credential_id: str) -> None:
        with self._lock:
            records = self._read_records()
            record = self._find(records, credential_id)
            records.remove(record)
            self._write_records(records)
            logger.info("Removed %s login %s", record["bank"], credential_id)

    def resolve(self, credential_id: str) -> tuple[str, SecretString, SecretString]:
        """The bank, username and password of a login, for the login flow only."""
        with self._lock:
            record = self._find(self._read_records(), credential_id)
            fernet = self._fernet(create=False)
            return (
                record["bank"],
                SecretString(_decrypt(fernet, record["username_enc"])),
                SecretString(_decrypt(fernet, record["password_enc"])),
            )

    # ── Internals ─────────────────────────────────────────────────────

    def _info(self, record: dict[str, Any], fernet: Fernet) -> CredentialInfo:
        return CredentialInfo(
            id=record["id"],
            bank=record["bank"],
            label=record["label"],
            username_hint=mask_username(_decrypt(fernet, record["username_enc"])),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    @staticmethod
    def _find(records: list[dict[str, Any]], credential_id: str) -> dict[str, Any]:
        for record in records:
            if record["id"] == credential_id:
                return record
        raise CredentialNotFound(f"No saved login with id {credential_id}")

    def _read_records(self) -> list[dict[str, Any]]:
        path = self._store_path
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise CredentialStoreError(f"Cannot read saved logins from {path}: {e}") from e
        return list(data.get("credentials", []))

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        self._ensure_directory()
        payload = json.dumps({"version": _STORE_VERSION, "credentials": records}, indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".credentials-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(payload)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self._store_path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _ensure_directory(self) -> None:
        directory = self.directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise CredentialStoreError(self._not_writable_message(directory, e)) from e
        if not os.access(directory, os.W_OK):
            raise CredentialStoreError(self._not_writable_message(directory, None))

    @staticmethod
    def _not_writable_message(directory: Path, error: OSError | None) -> str:
        reason = f" ({error})" if error else ""
        return (
            f"Cannot save logins: {directory} is not writable by uid {_uid()}{reason}. "
            "In Docker, the mounted data directory must be owned by the uid the container runs as "
            "(1000 with the provided docker-compose.yml)."
        )

    def _fernet(self, *, create: bool) -> Fernet:
        key_path = self._key_path
        if key_path.exists():
            try:
                return Fernet(key_path.read_bytes().strip())
            except (OSError, ValueError) as e:
                raise CredentialStoreError(f"Cannot read the encryption key {key_path}: {e}") from e
        if self._store_path.exists() and self._read_records():
            # A fresh key could not decrypt what is already saved, so making one would only
            # orphan those logins. Stop and say what is missing instead.
            raise CredentialStoreError(
                f"Saved logins exist in {self._store_path} but the encryption key {key_path} is missing. "
                "Restore secret.key from the same backup, or delete credentials.json and add the logins again."
            )
        if not create:
            raise CredentialStoreError(f"The encryption key {key_path} is missing.")
        self._ensure_directory()
        key = Fernet.generate_key()
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another process won the race; use its key.
            return Fernet(key_path.read_bytes().strip())
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(key)
        logger.info("Generated a new encryption key for saved logins at %s", key_path)
        return Fernet(key)


def _check_bank(bank: str) -> None:
    if bank not in BANKS:
        raise ValueError(f"Unknown bank {bank!r}; expected one of {', '.join(BANKS)}")


def _encrypt(fernet: Fernet, value: str) -> str:
    return fernet.encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(fernet: Fernet, token: str) -> str:
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise CredentialStoreError(
            "A saved login cannot be decrypted with the current secret.key; the key does not match "
            "the one the logins were saved with."
        ) from e


def _uid() -> str:
    getuid = getattr(os, "getuid", None)
    return str(getuid()) if getuid else "?"
