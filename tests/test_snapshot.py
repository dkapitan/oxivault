"""Snapshot containment, concurrency, and publication regressions."""

# The exception is raised on context exit after staged filesystem operations.
# ruff: noqa: PT012

from pathlib import Path
from unittest.mock import patch

import pytest

from oxivault.errors import ObjectConflictError, ObjectReadLimitError, PublicationError, StoreKeyError
from oxivault.snapshot import materialize_vault_snapshot
from oxivault.store.memory import MemoryStore


@pytest.mark.parametrize("existing", [False, True])
def test_publication_does_not_overwrite_concurrent_changes(existing):
    store = MemoryStore()
    if existing:
        store.put("note.md", b"original")
    with pytest.raises(PublicationError) as exc, materialize_vault_snapshot(store, sync_back=True) as root:
        (root / "note.md").write_bytes(b"conversion")
        store.put("note.md", b"concurrent")
    assert store.get("note.md") == b"concurrent"
    assert isinstance(exc.value.__cause__, ObjectConflictError)


def test_partial_publication_reports_applied_paths():
    store = MemoryStore()
    with pytest.raises(PublicationError) as exc, materialize_vault_snapshot(store, sync_back=True) as root:
        (root / "a.md").write_bytes(b"first")
        (root / "b.md").write_bytes(b"second")
        store.put("b.md", b"concurrent")
    assert store.get("a.md") == b"first"
    assert exc.value.applied_paths == ("a.md",)
    assert exc.value.failed_path == "b.md"


def test_partial_publication_reports_snapshot_read_failure():
    store = MemoryStore()
    original_open = Path.open

    def fail_second_read(path, *args, **kwargs):
        if path.name == "b.md" and args == ("rb",):
            raise OSError("staged read failed")
        return original_open(path, *args, **kwargs)

    with (
        patch.object(Path, "open", fail_second_read),
        pytest.raises(PublicationError) as exc,
        materialize_vault_snapshot(store, sync_back=True) as root,
    ):
        (root / "a.md").write_bytes(b"first")
        (root / "b.md").write_bytes(b"second")
    assert exc.value.applied_paths == ("a.md",)
    assert exc.value.failed_path == "b.md"


@pytest.mark.parametrize("directory", [False, True])
def test_snapshot_refuses_symlink_publication(tmp_path, directory):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_bytes(b"secret")
    target = outside if directory else outside / "secret.md"
    store = MemoryStore()
    with pytest.raises(StoreKeyError), materialize_vault_snapshot(store, sync_back=True) as root:
        (root / "a.md").write_bytes(b"must not publish")
        (root / "z").symlink_to(target, target_is_directory=directory)
    assert store.list() == []


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../b", "bad\x00key"])
def test_snapshot_validates_store_listing_before_reading(key):
    store = MemoryStore()
    with (
        patch.object(store, "list", return_value=[key]),
        patch.object(store, "get", return_value=b"data"),
        patch.object(Path, "write_bytes", side_effect=AssertionError("unsafe write")),
        pytest.raises(StoreKeyError),
        materialize_vault_snapshot(store),
    ):
        pytest.fail("invalid key reached the conversion")


def test_snapshot_failure_cleans_up_without_publishing():
    store = MemoryStore()
    with pytest.raises(ValueError, match="conversion"), materialize_vault_snapshot(store, sync_back=True) as root:
        (root / "note.md").write_bytes(b"staged")
        raise ValueError("conversion failed")
    assert not root.exists()
    assert store.list() == []


def test_unchanged_objects_are_not_written():
    store = MemoryStore()
    store.put("note.md", b"original")
    with (
        patch.object(store, "put", side_effect=AssertionError("unexpected write")),
        materialize_vault_snapshot(store, sync_back=True),
    ):
        pass
    assert store.get("note.md") == b"original"


def test_snapshot_does_not_keep_whole_vault_in_memory(tmp_path):
    import tracemalloc

    from oxivault.store.local import LocalDirStore

    store = LocalDirStore(tmp_path)
    for index in range(8):
        store.put(f"{index}.bin", b"x" * (1 << 20))
    tracemalloc.start()
    try:
        with materialize_vault_snapshot(store):
            current, _peak = tracemalloc.get_traced_memory()
            assert current < 3 << 20
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("limit", [7, 8])
def test_publication_checks_total_budget_before_writes(limit):
    store = MemoryStore()
    snapshot = materialize_vault_snapshot(store, sync_back=True, max_snapshot_bytes=limit)
    if limit == 7:
        with pytest.raises(ObjectReadLimitError), snapshot as root:
            (root / "a.md").write_bytes(b"1234")
            (root / "b.md").write_bytes(b"5678")
        assert store.list() == []
    else:
        with snapshot as root:
            (root / "a.md").write_bytes(b"1234")
            (root / "b.md").write_bytes(b"5678")
        assert store.list() == ["a.md", "b.md"]


def test_snapshot_refuses_object_changed_while_reading():
    store = MemoryStore()
    store.put("note.md", b"original")
    original_get = store.get

    def concurrent_get(key, *, max_bytes=None):
        content = original_get(key, max_bytes=max_bytes)
        store.put(key, b"concurrent")
        return content

    with (
        patch.object(store, "get", side_effect=concurrent_get),
        pytest.raises(ObjectConflictError),
        materialize_vault_snapshot(store),
    ):
        pytest.fail("inconsistent snapshot reached conversion")
    assert store.get("note.md") == b"concurrent"
