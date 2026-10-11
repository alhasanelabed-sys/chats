"""Password hashing, signed session tokens and permission checks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections import OrderedDict

from .config import settings

PERMISSIONS = [
    "personnel.view", "personnel.edit",
    "device.view", "device.control",
    "attendance.view", "attendance.edit", "attendance.approve",
    "reports.view",
    "system.admin",
]

_ITER = 200_000
MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH = 10, 200


def validate_password(password: str, identity: str = "", old_password: str = "") -> None:
    """Shared policy for new passwords; existing hashes remain valid until changed."""
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError("password must be between 10 and 200 characters")
    if password == old_password or (identity and password.casefold() == identity.strip().casefold()):
        raise ValueError("choose a new password different from your account name")
    if password.casefold() in {"1234567890", "12345678901", "123456789012", "password123", "qwerty12345",
                               "adminadmin", "administrator", "letmein123", "0000000000"}:
        raise ValueError("choose a less common password")


class LoginThrottle:
    """Bounded per-account and per-address failures for this server process.

    A successful login clears only the account's failures: rotating usernames must
    never bypass the address limit. Access is synchronized across request threads.
    """
    def __init__(self, max_account_failures: int = 5, max_ip_failures: int = 20,
                 window_seconds: int = 600, max_keys: int = 20000):
        self.max_account_failures, self.max_ip_failures = max_account_failures, max_ip_failures
        self.window_seconds, self.max_keys = window_seconds, max_keys
        self.accounts: OrderedDict[str, list[float]] = OrderedDict()
        self.addresses: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _recent(self, bucket, key: str, current: float) -> list[float]:
        recent = [t for t in bucket.get(key, ()) if current - t < self.window_seconds]
        bucket.pop(key, None)
        if recent:
            bucket[key] = recent
        return recent

    def check(self, account: str, ip: str) -> bool:
        with self._lock:
            current = time.monotonic()
            return (len(self._recent(self.accounts, account, current)) < self.max_account_failures
                    and len(self._recent(self.addresses, ip, current)) < self.max_ip_failures)

    def failure(self, account: str, ip: str) -> None:
        with self._lock:
            current = time.monotonic()
            for bucket, key, limit in ((self.accounts, account, self.max_account_failures),
                                        (self.addresses, ip, self.max_ip_failures)):
                bucket[key] = (self._recent(bucket, key, current) + [current])[-limit:]
                while len(bucket) > self.max_keys:
                    bucket.popitem(last=False)

    def success(self, account: str, ip: str = "") -> None:
        with self._lock:
            self.accounts.pop(account, None)

    def clear(self) -> None:
        with self._lock:
            self.accounts.clear()
            self.addresses.clear()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iters)
        if not 1 <= iterations <= 2_000_000 or len(password) > MAX_PASSWORD_LENGTH:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user_id: int, pw_hash: str, hours: int | None = None, kind: str = "") -> str:
    """Token = payload.signature. Changing the password (hash) invalidates tokens.
    ``kind`` "e" marks an employee-portal session, which never opens the admin side."""
    exp = int(time.time()) + 3600 * (hours or settings.session_hours)
    data = {"u": user_id, "e": exp, "p": hashlib.sha256(pw_hash.encode()).hexdigest()}
    if kind:
        data["k"] = kind
    payload = _b64(json.dumps(data).encode())
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    return payload + "." + _b64(sig)


def read_token(token: str) -> dict | None:
    try:
        if not isinstance(token, str) or len(token) > 4096:
            return None
        payload, sig = token.split(".")
        good = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(good), sig):
            return None
        data = json.loads(_unb64(payload))
        if (not isinstance(data, dict) or type(data.get("u")) is not int or data["u"] <= 0
                or type(data.get("e")) is not int or data["e"] <= time.time()
                or not isinstance(data.get("p"), str) or len(data["p"]) != 64
                or data.get("k", "") not in ("", "e")):
            return None
        return data
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return None


def token_password_matches(data: dict, pw_hash: str | None) -> bool:
    fingerprint = hashlib.sha256((pw_hash or "").encode()).hexdigest()
    return hmac.compare_digest(fingerprint, data.get("p", ""))


def user_permissions(user) -> set[str]:
    if user.is_superuser:
        return set(PERMISSIONS)
    if not user.role:
        return set()
    try:
        return set(json.loads(user.role.permissions or "[]"))
    except json.JSONDecodeError:
        return set()
