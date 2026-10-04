"""Tests for Git storage backend using dulwich in temporary repositories."""

from __future__ import annotations

import subprocess
import threading

import pytest

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
)
from oxivault.models import RdfDocument
from oxivault.store.git import GitStore
from oxivault.vault import Vault


def test_git_store_bare_mode_put_get_list(tmp_path) -> None:
    repo_dir = tmp_path / "bare_repo.git"
    store = GitStore.init_bare(repo_dir)

    key = "notes/hello.md"
    content = b"# Hello Git\n"

    # Put object in bare repo
    etag = store.put(key, content)
    assert isinstance(etag, str)
    assert len(etag) == 40  # SHA1 commit or blob hash

    # Exists and Get
    assert store.exists(key) is True
    assert store.get(key) == content

    # Stat
    stat = store.stat(key)
    assert stat.size == len(content)
    assert stat.etag == etag

    # List
    assert store.list() == ["notes/hello.md"]

    # History
    history = store.history(key)
    assert len(history) == 1
    assert history[0]["etag"] == etag

    # Conditional write matching etag
    etag2 = store.put(key, b"# Hello Git v2\n", expected_etag=etag)
    assert etag2 != etag
    assert store.get(key) == b"# Hello Git v2\n"

    # Stale etag fails
    with pytest.raises(ObjectConflictError):
        store.put(key, b"# Hello Git v3\n", expected_etag=etag)

    # Delete
    store.delete(key)
    assert store.exists(key) is False
    with pytest.raises(ObjectNotFoundError):
        store.get(key)


def test_git_store_working_tree_mode(tmp_path) -> None:
    repo_dir = tmp_path / "worktree_repo"
    store = GitStore.init_worktree(repo_dir)

    key = "recipe.md"
    content = b"# Delicious Hummus\n"
    etag = store.put(key, content)
    assert isinstance(etag, str)

    # File actually exists in working tree on disk
    assert (repo_dir / "recipe.md").exists()
    assert (repo_dir / "recipe.md").read_bytes() == content

    # Get and list
    assert store.get(key) == content
    assert store.list() == ["recipe.md"]

    # Git index must stay in sync with HEAD and working tree
    status = subprocess.run(
        ["/usr/bin/git", "status", "--short"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""


def test_git_vault_conversion_and_query(tmp_path) -> None:
    repo_dir = tmp_path / "vault_git.git"
    store = GitStore.init_bare(repo_dir)
    vault = Vault(store=store)

    rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ; rdfs:label "Ontology" .
cul:Recipe a owl:Class ; rdfs:label "Recipe" .
data:hummus a cul:Recipe ; rdfs:label "Hummus" .
"""
    vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])

    assert store.exists("hummus.md")
    assert store.exists("context.jsonld")

    triples = vault.triples()
    assert len(triples) >= 2
    assert "Hummus" in triples["object"].to_list()


def test_git_store_history_filters_to_path_changes(tmp_path) -> None:
    repo_dir = tmp_path / "history_repo.git"
    store = GitStore.init_bare(repo_dir)
    store.put("a.md", b"a1")
    store.put("b.md", b"b1")
    store.put("c.md", b"c1")

    history = store.history("a.md")
    assert len(history) == 1
    message = history[0]["message"]
    assert isinstance(message, str)
    assert "a.md" in message


def test_git_store_concurrent_puts_do_not_crash(tmp_path) -> None:
    repo_dir = tmp_path / "concurrency_repo.git"
    store = GitStore.init_bare(repo_dir)
    errors: list[Exception] = []

    def writer(prefix: str) -> None:
        try:
            for i in range(20):
                store.put(f"{prefix}-{i}.md", f"{prefix}-{i}".encode())
        except Exception as err:  # noqa: BLE001
            errors.append(err)

    t1 = threading.Thread(target=writer, args=("left",))
    t2 = threading.Thread(target=writer, args=("right",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
