"""Anonymous local client identity persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from uuid import uuid4

logger = logging.getLogger(__name__)


class ClientIdentityStore:
    """Persists a stable anonymous AuraBook client identifier."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._path = state_dir / "client_identity.json"
        self._lock = Lock()

    def get_client_id(self) -> str:
        """Return the stable anonymous client identifier."""
        with self._lock:
            if self._path.exists():
                try:
                    payload = json.loads(self._path.read_text(encoding="utf-8"))
                    client_id = payload["client_id"]
                    if isinstance(client_id, str) and client_id:
                        return client_id
                except (KeyError, OSError, ValueError, TypeError) as exc:
                    logger.warning("Failed to read persisted client identity: %s", exc)

            client_id = str(uuid4())
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"client_id": client_id}, indent=2),
                encoding="utf-8",
            )
            logger.info("Created new anonymous AuraBook client identity")
            return client_id

    def rotate_client_id(self) -> str:
        """Replace the persisted anonymous identifier with a new GUID."""
        with self._lock:
            client_id = str(uuid4())
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"client_id": client_id}, indent=2),
                encoding="utf-8",
            )
            logger.info("Rotated anonymous AuraBook client identity")
            return client_id
