"""``/credentials``: add, list, edit and remove saved bank logins.

The endpoints are write-only for secrets: a username or password goes in, and only the
label, the bank, the timestamps and a partly hidden username come back. Nothing here
decrypts a password for a caller.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, SecretStr

from pennyspy.credentials.store import Bank, CredentialNotFound, CredentialStore, CredentialStoreError


class CredentialCreate(BaseModel):
    bank: Bank
    label: str
    username: SecretStr
    password: SecretStr


class CredentialUpdate(BaseModel):
    """Fields left out or blank keep their current value."""

    label: str | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None


def _reveal(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def create_credentials_router(store: CredentialStore) -> APIRouter:
    router = APIRouter()

    @router.get("")
    def list_credentials(bank: Bank | None = Query(default=None)) -> list[dict[str, Any]]:
        try:
            return [asdict(info) for info in store.list_logins(bank)]
        except CredentialStoreError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("", status_code=201)
    def create_credential(body: CredentialCreate) -> dict[str, Any]:
        try:
            info = store.create(
                bank=body.bank,
                label=body.label,
                username=body.username.get_secret_value(),
                password=body.password.get_secret_value(),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except CredentialStoreError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return asdict(info)

    @router.put("/{credential_id}")
    def update_credential(credential_id: str, body: CredentialUpdate) -> dict[str, Any]:
        try:
            info = store.update(
                credential_id,
                label=body.label,
                username=_reveal(body.username),
                password=_reveal(body.password),
            )
        except CredentialNotFound as e:
            raise HTTPException(status_code=404, detail=e.args[0])
        except CredentialStoreError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return asdict(info)

    @router.delete("/{credential_id}", status_code=204)
    def delete_credential(credential_id: str) -> Response:
        try:
            store.delete(credential_id)
        except CredentialNotFound as e:
            raise HTTPException(status_code=404, detail=e.args[0])
        except CredentialStoreError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return Response(status_code=204)

    return router
