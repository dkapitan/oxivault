"""Tests for ObjectStore implementations."""

from __future__ import annotations

import pytest

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectReadLimitError,
    StoreKeyError,
)
from oxivault.store.base import ObjectStore
from oxivault.store.local import LocalDirStore
from oxivault.store.memory import MemoryStore


@pytest.fixture(params=["memory", "local"])
def store(request, tmp_path) -> ObjectStore:
    """Parametrized fixture providing MemoryStore and LocalDirStore instances."""
    if request.param == "memory":
        return MemoryStore()
    if request.param == "local":
        return LocalDirStore(root_dir=tmp_path / "vault")
    raise ValueError(f"Unknown store: {request.param}")


def test_put_and_get_roundtrip(store: ObjectStore) -> None:
    # GIVEN key and payload
    key = "notes/hello.md"
    content = b"# Hello world\n"

    # WHEN putting the object
    etag = store.put(key, content)

    # THEN getting it returns exact content and stat info matches
    assert store.get(key) == content
    assert store.exists(key) is True
    stat = store.stat(key)
    assert stat.size == len(content)
    assert stat.etag == etag
    assert isinstance(stat.etag, str)
    assert len(stat.etag) > 0


def test_get_nonexistent_raises_not_found(store: ObjectStore) -> None:
    # GIVEN a key that does not exist
    key = "missing.md"

    # WHEN getting or stating the key
    # THEN ObjectNotFoundError is raised
    with pytest.raises(ObjectNotFoundError):
        store.get(key)

    with pytest.raises(ObjectNotFoundError):
        store.stat(key)

    assert store.exists(key) is False


def test_put_atomic_replace(store: ObjectStore) -> None:
    # GIVEN an existing object
    key = "note.md"
    etag1 = store.put(key, b"version 1")

    # WHEN overwritten without precondition
    etag2 = store.put(key, b"version 2")

    # THEN content is updated and etag changes
    assert store.get(key) == b"version 2"
    assert etag1 != etag2


def test_delete_removes_object(store: ObjectStore) -> None:
    # GIVEN an existing object
    key = "note.md"
    store.put(key, b"to delete")
    assert store.exists(key) is True

    # WHEN deleting the object
    store.delete(key)

    # THEN it no longer exists
    assert store.exists(key) is False
    with pytest.raises(ObjectNotFoundError):
        store.get(key)


def test_delete_nonexistent_is_noop(store: ObjectStore) -> None:
    # GIVEN a missing key
    # WHEN deleting
    # THEN it does not raise
    store.delete("nonexistent.md")


def test_list_prefix_filtering(store: ObjectStore) -> None:
    # GIVEN several keys across folders
    store.put("Ontologies/Culinary/Recipe.md", b"class")
    store.put("Ontologies/Culinary/context.jsonld", b"{}")
    store.put("Recipes/hummus.md", b"recipe")
    store.put("context.jsonld", b"root")

    # WHEN listing with various prefixes
    all_keys = store.list()
    onto_keys = store.list("Ontologies/Culinary")
    recipes = store.list("Recipes/")

    # THEN results match exact prefixes and are sorted
    assert all_keys == [
        "Ontologies/Culinary/Recipe.md",
        "Ontologies/Culinary/context.jsonld",
        "Recipes/hummus.md",
        "context.jsonld",
    ]
    assert onto_keys == [
        "Ontologies/Culinary/Recipe.md",
        "Ontologies/Culinary/context.jsonld",
    ]
    assert recipes == ["Recipes/hummus.md"]


def test_bounded_get_enforces_limit(store: ObjectStore) -> None:
    # GIVEN an object of 20 bytes
    key = "large.md"
    store.put(key, b"01234567890123456789")

    # WHEN getting with max_bytes >= size
    # THEN it succeeds
    assert store.get(key, max_bytes=20) == b"01234567890123456789"

    # WHEN getting with max_bytes < size
    # THEN ObjectReadLimitError is raised
    with pytest.raises(ObjectReadLimitError):
        store.get(key, max_bytes=10)


def test_conditional_put_success_with_matching_etag(store: ObjectStore) -> None:
    # GIVEN an object with an etag
    key = "note.md"
    etag1 = store.put(key, b"initial")

    # WHEN putting with expected_etag matching current etag
    etag2 = store.put(key, b"updated", expected_etag=etag1)

    # THEN update succeeds and returns new etag
    assert store.get(key) == b"updated"
    assert etag2 != etag1


def test_conditional_put_conflict_with_stale_etag(store: ObjectStore) -> None:
    # GIVEN an object with an etag
    key = "note.md"
    etag1 = store.put(key, b"initial")
    store.put(key, b"updated by someone else")

    # WHEN putting with stale etag1
    # THEN ObjectConflictError is raised and content is untouched
    with pytest.raises(ObjectConflictError) as exc_info:
        store.put(key, b"should fail", expected_etag=etag1)

    assert exc_info.value.key == key
    assert store.get(key) == b"updated by someone else"


def test_conditional_put_create_only_precondition(store: ObjectStore) -> None:
    # GIVEN an empty store
    key = "new.md"

    # WHEN putting with expected_etag=None (create-only / if-none-match)
    store.put(key, b"created", expected_etag=None)
    assert store.get(key) == b"created"

    # WHEN attempting to create again with expected_etag=None
    # THEN ObjectConflictError is raised
    with pytest.raises(ObjectConflictError):
        store.put(key, b"second create", expected_etag=None)


@pytest.mark.parametrize(
    "invalid_key",
    [
        "",
        "/absolute/path.md",
        "../escape.md",
        "folder/../../escape.md",
        "folder/./current.md",
        "folder\\backslash.md",
    ],
)
def test_invalid_key_raises_store_key_error(store: ObjectStore, invalid_key: str) -> None:
    # GIVEN an invalid or escaping key
    # WHEN attempting store operations
    # THEN StoreKeyError is raised
    with pytest.raises(StoreKeyError):
        store.put(invalid_key, b"data")

    with pytest.raises(StoreKeyError):
        store.get(invalid_key)

    with pytest.raises(StoreKeyError):
        store.stat(invalid_key)

    with pytest.raises(StoreKeyError):
        store.delete(invalid_key)
