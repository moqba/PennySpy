"""Saved logins: encrypted at rest, never returned, and picked correctly at login."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pennyspy.credentials.resolve import (
    LoginCredentials,
    LoginSelectionError,
    credentials_or_env,
    resolve_login,
)
from pennyspy.credentials.router import create_credentials_router
from pennyspy.credentials.store import (
    CredentialNotFound,
    CredentialStore,
    CredentialStoreError,
    mask_username,
)
from pennyspy.scrapers.base import AuthStep, BankScraperInterface
from pennyspy.scrapers.router import create_scraper_router
from pennyspy.scrapers.session import ScraperSessionManager

USERNAME = "john.doe@example.com"
PASSWORD = "hunter2-correct-horse"

_LEGACY_VARS = (
    "PENNYSPY_BMOU",
    "PENNYSPY_BMOPP",
    "PENNYSPY_RBCU",
    "PENNYSPY_RBCP",
    "PENNYSPY_SCOTIAU",
    "PENNYSPY_SCOTIAP",
    "PENNYSPY_WSU",
    "PENNYSPY_WSP",
)


@pytest.fixture(autouse=True)
def _no_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _LEGACY_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path)


# ── Masking ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("username", "hint"),
    [
        ("john.doe@gmail.com", "jo•••@gmail.com"),
        ("jo@gmail.com", "•••@gmail.com"),
        ("12345678", "12•••78"),
        ("abcdef", "•••"),
        ("", "•••"),
    ],
)
def test_mask_username(username: str, hint: str) -> None:
    assert mask_username(username) == hint


# ── Store ─────────────────────────────────────────────────────────────


def test_create_round_trips_and_keeps_secrets_off_disk(store: CredentialStore, tmp_path: Path) -> None:
    info = store.create(bank="ws", label="Personal", username=USERNAME, password=PASSWORD)

    assert info.bank == "ws"
    assert info.label == "Personal"
    assert info.username_hint == "jo•••@example.com"
    bank, username, password = store.resolve(info.id)
    assert (bank, username.reveal(), password.reveal()) == ("ws", USERNAME, PASSWORD)

    on_disk = (tmp_path / "credentials.json").read_text(encoding="utf-8")
    assert USERNAME not in on_disk
    assert PASSWORD not in on_disk
    assert (tmp_path / "secret.key").is_file()


def test_resolved_secrets_do_not_print(store: CredentialStore) -> None:
    info = store.create(bank="bmo", label="Main", username=USERNAME, password=PASSWORD)
    _, username, password = store.resolve(info.id)
    assert PASSWORD not in f"{username} {password} {username!r} {password!r}"


def test_key_is_reused_across_instances(store: CredentialStore, tmp_path: Path) -> None:
    info = store.create(bank="rbc", label="Main", username=USERNAME, password=PASSWORD)
    _, _, password = CredentialStore(tmp_path).resolve(info.id)
    assert password.reveal() == PASSWORD


def test_missing_key_with_saved_logins_is_refused(store: CredentialStore, tmp_path: Path) -> None:
    store.create(bank="rbc", label="Main", username=USERNAME, password=PASSWORD)
    (tmp_path / "secret.key").unlink()

    with pytest.raises(CredentialStoreError, match="secret.key"):
        store.create(bank="rbc", label="Other", username="x", password="y")
    assert not (tmp_path / "secret.key").exists()


def test_wrong_key_is_reported(store: CredentialStore, tmp_path: Path) -> None:
    store.create(bank="rbc", label="Main", username=USERNAME, password=PASSWORD)
    (tmp_path / "secret.key").unlink()
    CredentialStore(tmp_path / "other").create(bank="rbc", label="x", username="x", password="y")
    (tmp_path / "other" / "secret.key").replace(tmp_path / "secret.key")

    with pytest.raises(CredentialStoreError, match="does not match"):
        store.list_logins()


def test_update_keeps_blank_fields(store: CredentialStore) -> None:
    info = store.create(bank="scotia", label="Main", username=USERNAME, password=PASSWORD)

    updated = store.update(info.id, label="  ", username="", password="new-password")

    assert updated.label == "Main"
    assert updated.username_hint == info.username_hint
    _, username, password = store.resolve(info.id)
    assert (username.reveal(), password.reveal()) == (USERNAME, "new-password")


def test_delete_and_filter(store: CredentialStore) -> None:
    a = store.create(bank="bmo", label="A", username="aaaaaaaa", password="p")
    b = store.create(bank="ws", label="B", username="bbbbbbbb", password="p")

    assert [i.id for i in store.list_logins("bmo")] == [a.id]
    store.delete(a.id)
    assert [i.id for i in store.list_logins()] == [b.id]
    with pytest.raises(CredentialNotFound):
        store.delete(a.id)


@pytest.mark.parametrize(
    "fields",
    [
        {"bank": "td", "label": "x", "username": "u", "password": "p"},
        {"bank": "bmo", "label": " ", "username": "u", "password": "p"},
        {"bank": "bmo", "label": "x", "username": "u", "password": ""},
    ],
)
def test_create_rejects_bad_input(store: CredentialStore, fields: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        store.create(**fields)


def test_empty_store_lists_nothing(store: CredentialStore, tmp_path: Path) -> None:
    assert store.list_logins() == []
    assert not (tmp_path / "secret.key").exists()


# ── Login selection ───────────────────────────────────────────────────


def test_resolve_explicit_id(store: CredentialStore) -> None:
    store.create(bank="ws", label="A", username="first-user", password="p1")
    second = store.create(bank="ws", label="B", username="second-user", password="p2")

    login = resolve_login("ws", second.id, store)
    assert login.password.reveal() == "p2"


def test_resolve_single_saved_login(store: CredentialStore) -> None:
    store.create(bank="ws", label="A", username=USERNAME, password=PASSWORD)
    assert resolve_login("ws", None, store).username.reveal() == USERNAME


def test_resolve_ambiguous_without_id(store: CredentialStore) -> None:
    store.create(bank="ws", label="A", username="first-user", password="p1")
    store.create(bank="ws", label="B", username="second-user", password="p2")
    with pytest.raises(LoginSelectionError, match="credential_id"):
        resolve_login("ws", None, store)


def test_resolve_wrong_bank(store: CredentialStore) -> None:
    info = store.create(bank="ws", label="A", username=USERNAME, password=PASSWORD)
    with pytest.raises(LoginSelectionError, match="not bmo"):
        resolve_login("bmo", info.id, store)


def test_resolve_unknown_id(store: CredentialStore) -> None:
    with pytest.raises(CredentialNotFound):
        resolve_login("ws", "nope", store)


def test_resolve_falls_back_to_env(store: CredentialStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENNYSPY_RBCU", "env-user")
    monkeypatch.setenv("PENNYSPY_RBCP", "env-pass")
    login = resolve_login("rbc", None, store)
    assert (login.username.reveal(), login.password.reveal()) == ("env-user", "env-pass")


def test_resolve_nothing_configured(store: CredentialStore) -> None:
    with pytest.raises(LoginSelectionError, match="Logins page"):
        resolve_login("scotia", None, store)


def test_credentials_or_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="PENNYSPY_BMOU"):
        credentials_or_env("bmo", None)
    monkeypatch.setenv("PENNYSPY_BMOU", "u")
    monkeypatch.setenv("PENNYSPY_BMOPP", "p")
    assert credentials_or_env("bmo", None).password.reveal() == "p"


# ── HTTP: /credentials ────────────────────────────────────────────────


@pytest.fixture
def client(store: CredentialStore) -> TestClient:
    app = FastAPI()
    app.include_router(create_credentials_router(store), prefix="/credentials")
    return TestClient(app)


def _assert_no_secrets(response: Any) -> None:
    assert USERNAME not in response.text
    assert PASSWORD not in response.text


def test_api_crud_never_returns_secrets(client: TestClient) -> None:
    created = client.post(
        "/credentials", json={"bank": "bmo", "label": "Joint", "username": USERNAME, "password": PASSWORD}
    )
    assert created.status_code == 201
    _assert_no_secrets(created)
    body = created.json()
    assert set(body) == {"id", "bank", "label", "username_hint", "created_at", "updated_at"}
    assert body["username_hint"] == "jo•••@example.com"

    listed = client.get("/credentials", params={"bank": "bmo"})
    _assert_no_secrets(listed)
    assert [c["id"] for c in listed.json()] == [body["id"]]
    assert client.get("/credentials", params={"bank": "ws"}).json() == []

    updated = client.put(f"/credentials/{body['id']}", json={"label": "Joint 2", "password": "other"})
    assert updated.status_code == 200
    _assert_no_secrets(updated)
    assert updated.json()["label"] == "Joint 2"

    assert client.delete(f"/credentials/{body['id']}").status_code == 204
    assert client.get("/credentials").json() == []


def test_api_unknown_id(client: TestClient) -> None:
    assert client.put("/credentials/nope", json={"label": "x"}).status_code == 404
    assert client.delete("/credentials/nope").status_code == 404


def test_api_validation_error_hides_password(client: TestClient) -> None:
    response = client.post(
        "/credentials", json={"bank": "td", "label": "x", "username": USERNAME, "password": PASSWORD}
    )
    assert response.status_code == 422
    _assert_no_secrets(response)


# ── HTTP: /<bank>/login picks the saved login ─────────────────────────


class _FakeScraper(BankScraperInterface):
    seen: list[dict[str, Any]] = []

    def start_auth(self, **kwargs: Any) -> AuthStep:
        _FakeScraper.seen.append(kwargs)
        return AuthStep(status="authenticated")

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        return AuthStep(status="authenticated")

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        raise NotImplementedError

    def quit(self) -> None:
        pass


@pytest.fixture
def login_client(store: CredentialStore) -> TestClient:
    from pydantic import BaseModel

    class _ScrapeParams(BaseModel):
        session_id: str

    _FakeScraper.seen = []
    app = FastAPI()
    app.include_router(
        create_scraper_router(
            bank="rbc",
            scraper_type=_FakeScraper,
            scrape_params_model=_ScrapeParams,
            session_manager=ScraperSessionManager(),
            credential_store=store,
        ),
        prefix="/rbc",
    )
    return TestClient(app)


def test_login_with_credential_id(login_client: TestClient, store: CredentialStore) -> None:
    store.create(bank="rbc", label="A", username="first-user", password="p1")
    chosen = store.create(bank="rbc", label="B", username="second-user", password="p2")

    response = login_client.post("/rbc/login", json={"credential_id": chosen.id})

    assert response.status_code == 200
    credentials = _FakeScraper.seen[-1]["credentials"]
    assert isinstance(credentials, LoginCredentials)
    assert credentials.password.reveal() == "p2"
    assert "credential_id" not in _FakeScraper.seen[-1]


def test_login_without_body_still_works(login_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENNYSPY_RBCU", "env-user")
    monkeypatch.setenv("PENNYSPY_RBCP", "env-pass")
    response = login_client.post("/rbc/login")
    assert response.status_code == 200
    assert _FakeScraper.seen[-1]["credentials"].username.reveal() == "env-user"


def test_login_errors(login_client: TestClient, store: CredentialStore) -> None:
    assert login_client.post("/rbc/login", json={"credential_id": "nope"}).status_code == 404
    assert login_client.post("/rbc/login").status_code == 400
    other_bank = store.create(bank="ws", label="A", username=USERNAME, password=PASSWORD)
    assert login_client.post("/rbc/login", json={"credential_id": other_bank.id}).status_code == 400
    assert _FakeScraper.seen == []


def test_store_file_is_versioned_json(store: CredentialStore, tmp_path: Path) -> None:
    store.create(bank="bmo", label="A", username=USERNAME, password=PASSWORD)
    data = json.loads((tmp_path / "credentials.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert set(data["credentials"][0]) == {
        "id",
        "bank",
        "label",
        "username_enc",
        "password_enc",
        "created_at",
        "updated_at",
    }
