"""Permissions for data-platform APIs.

Modes (DATA_PLATFORM_AUTH_MODE):
  dev      local development: every request is the "local-dev" principal with all permissions
  api_key  requests must send `X-API-Key: <key>` (or `Authorization: Bearer <key>`); keys are stored
           hashed in dp.api_keys with an explicit permission list (create with `python -m data_platform api-key`)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from .. import db
from ..config import get_settings

VIEW = "data_platform.view"
RUN_PIPELINE = "data_platform.run_pipeline"
CANCEL_PIPELINE = "data_platform.cancel_pipeline"
CONFIGURE_SOURCE = "data_platform.configure_source"
VIEW_RAW_DATA = "data_platform.view_raw_data"
EXPORT_DATA = "data_platform.export_data"
REVIEW_MATCHES = "data_platform.review_matches"

ALL_PERMISSIONS = {VIEW, RUN_PIPELINE, CANCEL_PIPELINE, CONFIGURE_SOURCE, VIEW_RAW_DATA, EXPORT_DATA, REVIEW_MATCHES}


@dataclass(frozen=True)
class Principal:
    name: str
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(name: str, key: str, permissions: list[str]) -> None:
    unknown = set(permissions) - ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"Unknown permissions: {', '.join(sorted(unknown))}")
    with db.control() as conn:
        conn.execute("INSERT INTO dp.api_keys (name, key_prefix, key_hash, permissions) VALUES (%s, %s, %s, %s)",
                     [name, key[:8], _hash(key), permissions])


def current_principal(request: Request) -> Principal:
    if get_settings().auth_mode == "dev":
        return Principal("local-dev", frozenset(ALL_PERMISSIONS))
    key = request.headers.get("x-api-key")
    authorization = request.headers.get("authorization", "")
    if not key and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    if not key:
        raise HTTPException(401, "API key required")
    with db.control() as conn:
        row = conn.execute("""
            UPDATE dp.api_keys SET last_used_at = now() WHERE key_hash = %s AND active
            RETURNING name, permissions
        """, [_hash(key)]).fetchone()
    if row is None:
        raise HTTPException(401, "Invalid API key")
    return Principal(row["name"], frozenset(row["permissions"]))


def require(permission: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(403, f"Missing permission {permission}")
        return principal

    return dependency
