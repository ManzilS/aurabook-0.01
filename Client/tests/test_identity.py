"""Tests for anonymous client identity persistence."""

from __future__ import annotations

from aurabook.services.identity import ClientIdentityStore


def test_identity_store_persists_client_id(tmp_path) -> None:
    store = ClientIdentityStore(tmp_path)

    first = store.get_client_id()
    second = store.get_client_id()

    assert first == second
    assert (tmp_path / "client_identity.json").exists()


def test_identity_store_can_rotate_client_id(tmp_path) -> None:
    store = ClientIdentityStore(tmp_path)

    first = store.get_client_id()
    second = store.rotate_client_id()

    assert first != second
    assert store.get_client_id() == second
