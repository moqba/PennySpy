"""Pick the username and password a bank login should use."""

from __future__ import annotations

import os
from dataclasses import dataclass
from logging import getLogger
from typing import Final

from pennyspy.credentials.store import CredentialStore
from pennyspy.scrapers.get_required_env_var import SecretString

logger = getLogger(__name__)

# The env vars each bank read before logins could be saved from the web UI. They still work
# as a fallback, so existing setups and direct Python use keep running unchanged.
LEGACY_ENV_VARS: Final[dict[str, tuple[str, str]]] = {
    "bmo": ("PENNYSPY_BMOU", "PENNYSPY_BMOPP"),
    "rbc": ("PENNYSPY_RBCU", "PENNYSPY_RBCP"),
    "scotia": ("PENNYSPY_SCOTIAU", "PENNYSPY_SCOTIAP"),
    "ws": ("PENNYSPY_WSU", "PENNYSPY_WSP"),
}


@dataclass(frozen=True)
class LoginCredentials:
    username: SecretString
    password: SecretString


class LoginSelectionError(ValueError):
    """No login can be picked; the message tells the user what to do."""


def env_credentials(bank: str) -> LoginCredentials | None:
    """The legacy env-var login for ``bank``, or ``None`` when either var is unset or empty."""
    user_var, password_var = LEGACY_ENV_VARS[bank]
    username, password = os.environ.get(user_var), os.environ.get(password_var)
    if not username or not password:
        return None
    return LoginCredentials(SecretString(username), SecretString(password))


def resolve_login(bank: str, credential_id: str | None, store: CredentialStore) -> LoginCredentials:
    """The login to use, in this order:

    1. the saved login ``credential_id`` names;
    2. the bank's only saved login, when it has exactly one;
    3. the legacy env vars.

    Raises :class:`CredentialNotFound` for an unknown id and :class:`LoginSelectionError`
    when the choice is wrong or ambiguous.
    """
    if credential_id:
        saved_bank, username, password = store.resolve(credential_id)
        if saved_bank != bank:
            raise LoginSelectionError(f"Saved login {credential_id} is for {saved_bank}, not {bank}")
        logger.info("Using saved %s login %s", bank, credential_id)
        return LoginCredentials(username, password)

    saved = store.list_logins(bank)
    if len(saved) == 1:
        logger.info("Using the only saved %s login %s", bank, saved[0].id)
        return resolve_login(bank, saved[0].id, store)
    if len(saved) > 1:
        raise LoginSelectionError(
            f"{len(saved)} {bank} logins are saved; pick one by passing its credential_id "
            f"(ids: {', '.join(info.id for info in saved)})"
        )

    from_env = env_credentials(bank)
    if from_env is not None:
        logger.info("No saved %s login; using the %s/%s env vars", bank, *LEGACY_ENV_VARS[bank])
        return from_env
    raise LoginSelectionError(f"No {bank} login is saved. Add one on the Logins page of the web UI.")


def credentials_or_env(bank: str, credentials: LoginCredentials | None) -> LoginCredentials:
    """``credentials`` when the caller passed some, else the legacy env vars.

    Lets a scraper be driven straight from Python, without the API and its saved logins.
    """
    if credentials is not None:
        return credentials
    from_env = env_credentials(bank)
    if from_env is None:
        user_var, password_var = LEGACY_ENV_VARS[bank]
        raise ValueError(f"No {bank} login was given and {user_var}/{password_var} are not both set.")
    return from_env
