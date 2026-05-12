"""Persisted per-user preferences for AuraBook local behavior."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field

from aurabook.core.models import ResponseGradient


class RepoPolicy(BaseModel):
    """Per-repository behavior overrides."""

    max_gradient: ResponseGradient = ResponseGradient.FULL
    cloud_enabled: bool = True
    upload_enabled: bool = True
    suppressed_note_ids: list[str] = Field(default_factory=list)
    suppressed_trajectory_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class UserPreferences(BaseModel):
    """Persisted local user preferences."""

    max_gradient: ResponseGradient = ResponseGradient.FULL
    repo_profiles: dict[str, RepoPolicy] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class UserPreferencesStore:
    """Reads and writes local user preference overrides."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def load(self) -> UserPreferences:
        """Return the current stored preferences."""
        with self._lock:
            if not self._path.exists():
                return UserPreferences()
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return UserPreferences()
            return UserPreferences.model_validate(raw)

    def save(self, preferences: UserPreferences) -> None:
        """Persist the provided preferences."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(preferences.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )

    def set_max_gradient(self, gradient: ResponseGradient) -> UserPreferences:
        """Persist a new maximum community intervention gradient."""
        preferences = self.load()
        preferences.max_gradient = gradient
        self.save(preferences)
        return preferences

    def get_repo_policy(self, repo_root: Path) -> RepoPolicy | None:
        """Return the effective repo policy for the provided repository root."""
        preferences = self.load()
        return preferences.repo_profiles.get(str(repo_root.resolve()))

    def set_repo_policy(
        self,
        repo_root: Path,
        *,
        max_gradient: ResponseGradient | None = None,
        cloud_enabled: bool | None = None,
        upload_enabled: bool | None = None,
    ) -> UserPreferences:
        """Create or update a per-repository policy override."""
        preferences = self.load()
        key = str(repo_root.resolve())
        policy = preferences.repo_profiles.get(key, RepoPolicy())
        if max_gradient is not None:
            policy.max_gradient = max_gradient
        if cloud_enabled is not None:
            policy.cloud_enabled = cloud_enabled
        if upload_enabled is not None:
            policy.upload_enabled = upload_enabled
        preferences.repo_profiles[key] = policy
        self.save(preferences)
        return preferences

    def suppress_note(self, repo_root: Path, note_id: str) -> UserPreferences:
        """Suppress a specific note for the provided repo."""
        preferences = self.load()
        key = str(repo_root.resolve())
        policy = preferences.repo_profiles.get(key, RepoPolicy())
        if note_id not in policy.suppressed_note_ids:
            policy.suppressed_note_ids.append(note_id)
        preferences.repo_profiles[key] = policy
        self.save(preferences)
        return preferences

    def suppress_trajectory(self, repo_root: Path, trajectory_id: str) -> UserPreferences:
        """Suppress a specific trajectory for the provided repo."""
        preferences = self.load()
        key = str(repo_root.resolve())
        policy = preferences.repo_profiles.get(key, RepoPolicy())
        if trajectory_id not in policy.suppressed_trajectory_ids:
            policy.suppressed_trajectory_ids.append(trajectory_id)
        preferences.repo_profiles[key] = policy
        self.save(preferences)
        return preferences

    def set_private_mode(self, repo_root: Path, *, enabled: bool) -> UserPreferences:
        """Enable or disable repo-level private mode."""
        return self.set_repo_policy(
            repo_root,
            cloud_enabled=not enabled,
            upload_enabled=not enabled,
        )
