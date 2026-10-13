"""Admin credential check and a small login throttle."""

from __future__ import annotations

import hmac
import threading
import time


def credentials_match(
    given_username: str,
    given_password: str,
    expected_username: str,
    expected_password: str,
) -> bool:
    """Constant-time comparison for both fields (no short-circuit on username)."""
    user_ok = hmac.compare_digest(given_username.encode(), expected_username.encode())
    pass_ok = hmac.compare_digest(given_password.encode(), expected_password.encode())
    return user_ok and pass_ok


class LoginThrottle:
    """Locks a client key out for a while after repeated failed logins.

    In-memory and per-process: enough for a single-admin local or small-VPS
    deployment. It resets when the server restarts.
    """

    def __init__(self, max_failures: int = 5, lock_seconds: float = 300.0) -> None:
        self._max_failures = max_failures
        self._lock_seconds = lock_seconds
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}
        self._mutex = threading.Lock()

    def retry_after(self, key: str) -> float:
        """Seconds left on the lock for this key, or 0 if not locked."""
        with self._mutex:
            until = self._locked_until.get(key, 0.0)
            remaining = until - time.monotonic()
            if remaining > 0:
                return remaining
            self._locked_until.pop(key, None)
            return 0.0

    def record_failure(self, key: str) -> None:
        with self._mutex:
            count = self._failures.get(key, 0) + 1
            if count >= self._max_failures:
                self._locked_until[key] = time.monotonic() + self._lock_seconds
                self._failures.pop(key, None)
            else:
                self._failures[key] = count

    def record_success(self, key: str) -> None:
        with self._mutex:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)
