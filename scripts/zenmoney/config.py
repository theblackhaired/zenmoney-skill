from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent.parent
_cfg_path = ROOT / "config.json"
TOKEN_ENV_VAR = "ZENMONEY_TOKEN"
CACHE_PATH = ROOT / ".cache.json"
BASE_URL = "https://api.zenmoney.ru"


class StateStoreError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "code": self.code,
            "error": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class CorruptStateError(StateStoreError):
    def __init__(self, path: Path, reason: str):
        super().__init__(
            "CORRUPT_STATE",
            f"State file is not valid JSON: {path}",
            {"path": str(path), "reason": reason},
        )


class LostUpdateError(StateStoreError):
    def __init__(
        self,
        path: Path,
        current_timestamp: int,
        disk_timestamp: int,
        expected_timestamp: int | None = None,
    ):
        details: dict[str, Any] = {
            "path": str(path),
            "current_serverTimestamp": current_timestamp,
            "disk_serverTimestamp": disk_timestamp,
        }
        if expected_timestamp is not None:
            details["expected_serverTimestamp"] = expected_timestamp
        super().__init__(
            "LOST_UPDATE",
            "Refusing to overwrite newer ZenMoney cache state",
            details,
        )


class _FileLock:
    def __init__(self, path: Path, timeout: float = 10.0, poll_interval: float = 0.05):
        self.path = path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fh: Any | None = None

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._lock()
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    raise TimeoutError(f"Timed out acquiring state lock: {self.path}") from None
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._fh is not None:
                self._unlock()
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def _lock(self) -> None:
        assert self._fh is not None
        if os.name == "nt":
            import msvcrt

            self._fh.seek(0)
            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError(str(exc)) from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BlockingIOError(str(exc)) from exc

    def _unlock(self) -> None:
        assert self._fh is not None
        if os.name == "nt":
            import msvcrt

            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def state_file_lock(path: Path) -> Iterator[None]:
    with _FileLock(_state_lock_path(path)):
        yield


def _state_lock_path(path: Path) -> Path:
    resolved = str(path.resolve())
    digest = sha256(resolved.encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "zenmoney-state-locks" / f"{digest}.lock"


def read_json_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorruptStateError(path, str(exc)) from exc
    if not isinstance(raw, dict):
        raise CorruptStateError(path, "top-level JSON value must be an object")
    return raw


def write_json_state_atomic(path: Path, payload: dict[str, Any], *, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def _load_config() -> dict[str, Any]:
    with state_file_lock(_cfg_path):
        return read_json_state(_cfg_path)


def save_config(cfg: dict[str, Any]) -> None:
    with state_file_lock(_cfg_path):
        write_json_state_atomic(_cfg_path, cfg, indent=2)


def setup_budget_mode_config(mode: str) -> dict[str, Any]:
    with state_file_lock(_cfg_path):
        cfg = read_json_state(_cfg_path)
        cfg["budget_mode"] = mode
        cfg["budget_mode_configured"] = True
        write_json_state_atomic(_cfg_path, cfg, indent=2)
        return cfg


CONFIG_LOAD_ERROR: StateStoreError | None = None


def _resolve_token() -> str:
    global CONFIG_LOAD_ERROR
    env_token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if env_token:
        return env_token

    try:
        cfg_token = _load_config().get("token", "")
    except StateStoreError as exc:
        CONFIG_LOAD_ERROR = exc
        return ""
    return cfg_token.strip() if isinstance(cfg_token, str) else ""

TOKEN = _resolve_token()
