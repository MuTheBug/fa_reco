"""Password hashing and roles."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

ITERATIONS = 240_000

ROLES = {
    "admin": "مدير النظام",
    "supervisor": "مشرف",
    "viewer": "مطّلع",
}

# What each role may do.
PERMISSIONS = {
    "admin": {"view", "edit", "correct", "enroll", "settings", "users", "backup"},
    "supervisor": {"view", "edit", "correct", "enroll"},
    "viewer": {"view"},
}


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def can(role: str | None, permission: str) -> bool:
    return bool(role) and permission in PERMISSIONS.get(role, set())
