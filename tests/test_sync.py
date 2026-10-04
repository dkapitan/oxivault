"""Tests for Obsidian sync manifest and sync engine."""

from __future__ import annotations

from oxivault.store.local import LocalDirStore
from oxivault.store.memory import MemoryStore
from oxivault.sync import SyncEngine


def test_sync_pull_materializes_store_to_local(tmp_path) -> None:
    # GIVEN a remote store with initial notes
    remote = MemoryStore()
    remote.put("note1.md", b"# Note 1\n")
    remote.put("sub/note2.md", b"# Note 2\n")

    local_dir = tmp_path / "local_vault"
    local = LocalDirStore(root_dir=local_dir)

    engine = SyncEngine(local=local, remote=remote)

    # WHEN executing pull
    result = engine.pull()

    # THEN local has the files and manifest is written
    assert "note1.md" in result.downloaded
    assert "sub/note2.md" in result.downloaded
    assert local.get("note1.md") == b"# Note 1\n"
    assert local.get("sub/note2.md") == b"# Note 2\n"

    # AND state manifest exists
    assert local.exists(".oxivault/state.json")

    # AND running pull again is idempotent (no-op)
    noop_result = engine.pull()
    assert noop_result.downloaded == []
    assert noop_result.conflicts == []


def test_sync_push_uploads_local_changes(tmp_path) -> None:
    remote = MemoryStore()
    local_dir = tmp_path / "local_vault"
    local = LocalDirStore(root_dir=local_dir)
    engine = SyncEngine(local=local, remote=remote)

    # Initial sync
    engine.pull()

    # Local creates a new note
    local.put("new_note.md", b"# Local new\n")

    # WHEN executing push
    push_res = engine.push()

    # THEN note is uploaded to remote and manifest is updated
    assert "new_note.md" in push_res.uploaded
    assert remote.get("new_note.md") == b"# Local new\n"

    # AND pushing again is no-op
    push_noop = engine.push()
    assert push_noop.uploaded == []


def test_sync_push_detects_conflict_and_creates_conflict_file(tmp_path) -> None:
    remote = MemoryStore()
    remote.put("recipe.md", b"# Hummus Original\n")

    local_dir = tmp_path / "local_vault"
    local = LocalDirStore(root_dir=local_dir)
    engine = SyncEngine(local=local, remote=remote)

    # 1. Pull down original
    engine.pull()

    # 2. Local edits recipe.md
    local.put("recipe.md", b"# Hummus Local Edit\n")

    # 3. Concurrent out-of-band edit happens on remote
    remote.put("recipe.md", b"# Hummus Remote Concurrent Edit\n")

    # 4. Push should fail conditional write, save conflict copy locally, and keep remote edit
    push_res = engine.push()
    assert len(push_res.conflicts) == 1
    assert "recipe.md" in push_res.conflicts

    # Verify conflict file exists on local
    assert local.exists("recipe.conflict.md")
    assert local.get("recipe.conflict.md") == b"# Hummus Local Edit\n"
    # Remote remains untouched by conflicting local edit
    assert remote.get("recipe.md") == b"# Hummus Remote Concurrent Edit\n"


def test_sync_pull_detects_local_remote_conflict_and_preserves_local_edit(tmp_path) -> None:
    remote = MemoryStore()
    remote.put("recipe.md", b"# Hummus Original\n")

    local_dir = tmp_path / "local_vault"
    local = LocalDirStore(root_dir=local_dir)
    engine = SyncEngine(local=local, remote=remote)

    # Baseline sync
    engine.pull()

    # Local and remote diverge
    local.put("recipe.md", b"# Hummus Local Edit\n")
    remote.put("recipe.md", b"# Hummus Remote Concurrent Edit\n")

    # Pull must not silently discard local edit
    pull_res = engine.pull()
    assert "recipe.md" in pull_res.conflicts
    assert local.exists("recipe.conflict.md")
    assert local.get("recipe.conflict.md") == b"# Hummus Local Edit\n"
    assert local.get("recipe.md") == b"# Hummus Remote Concurrent Edit\n"
