"""Tests for persisted user preferences."""

from __future__ import annotations

from pathlib import Path

from aurabook.core.models import ResponseGradient
from aurabook.services.preferences import UserPreferencesStore


def test_preferences_store_round_trips_gradient(tmp_path: Path) -> None:
    store = UserPreferencesStore(tmp_path / "preferences.json")

    preferences = store.set_max_gradient(ResponseGradient.NOTES)
    reloaded = store.load()

    assert preferences.max_gradient == ResponseGradient.NOTES
    assert reloaded.max_gradient == ResponseGradient.NOTES


def test_preferences_store_persists_repo_policy_and_suppressions(tmp_path: Path) -> None:
    store = UserPreferencesStore(tmp_path / "preferences.json")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    store.set_repo_policy(
        repo_root,
        max_gradient=ResponseGradient.GUIDE,
        cloud_enabled=False,
        upload_enabled=False,
    )
    store.suppress_note(repo_root, "note-1")
    store.suppress_trajectory(repo_root, "traj-1")

    policy = store.get_repo_policy(repo_root)

    assert policy is not None
    assert policy.max_gradient == ResponseGradient.GUIDE
    assert policy.cloud_enabled is False
    assert policy.upload_enabled is False
    assert policy.suppressed_note_ids == ["note-1"]
    assert policy.suppressed_trajectory_ids == ["traj-1"]


def test_preferences_store_private_mode_disables_cloud_and_upload(tmp_path: Path) -> None:
    store = UserPreferencesStore(tmp_path / "preferences.json")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    store.set_private_mode(repo_root, enabled=True)

    policy = store.get_repo_policy(repo_root)

    assert policy is not None
    assert policy.cloud_enabled is False
    assert policy.upload_enabled is False
