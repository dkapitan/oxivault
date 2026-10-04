"""Tests for S3 ObjectStore backend using moto."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectReadLimitError,
)
from oxivault.models import RdfDocument
from oxivault.store.s3 import S3Store
from oxivault.vault import Vault


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-vault")
        yield "test-vault"


def test_s3_store_put_get_stat_roundtrip(s3_bucket: str) -> None:
    store = S3Store(bucket=s3_bucket, prefix="vault/")

    key = "notes/hello.md"
    content = b"# Hello S3\n"
    etag = store.put(key, content)

    assert store.exists(key) is True
    assert store.get(key) == content
    stat = store.stat(key)
    assert stat.size == len(content)
    assert stat.etag == etag

    # List with prefix scoping
    assert store.list() == ["notes/hello.md"]

    # Delete
    store.delete(key)
    assert store.exists(key) is False
    with pytest.raises(ObjectNotFoundError):
        store.get(key)


def test_s3_store_conditional_writes(s3_bucket: str) -> None:
    store = S3Store(bucket=s3_bucket)

    key = "doc.md"
    etag1 = store.put(key, b"v1", expected_etag=None)
    assert isinstance(etag1, str)

    # Colliding create-only fails
    with pytest.raises(ObjectConflictError):
        store.put(key, b"collide", expected_etag=None)

    # Matching ETag update succeeds
    etag2 = store.put(key, b"v2", expected_etag=etag1)
    assert etag2 != etag1

    # Stale ETag update fails
    with pytest.raises(ObjectConflictError):
        store.put(key, b"v3", expected_etag=etag1)


def test_s3_store_bounded_read(s3_bucket: str) -> None:
    store = S3Store(bucket=s3_bucket)
    key = "big.bin"
    store.put(key, b"1234567890")

    with pytest.raises(ObjectReadLimitError):
        store.get(key, max_bytes=5)


def test_s3_store_bounded_read_empty_object(s3_bucket: str) -> None:
    store = S3Store(bucket=s3_bucket)
    store.put("empty.md", b"")
    assert store.get("empty.md", max_bytes=1024) == b""


def test_s3_vault_conversion(s3_bucket: str) -> None:
    store = S3Store(bucket=s3_bucket, prefix="myvault/")
    vault = Vault(store=store)

    rdf = b"""@prefix cul: <https://example.org/culinary#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix data: <https://example.org/data/> .

cul:Culinary a owl:Ontology ; rdfs:label "Ontology" .
cul:Recipe a owl:Class ; rdfs:label "Recipe" .
data:hummus a cul:Recipe ; rdfs:label "Hummus" .
"""
    report = vault.rdf2vault([RdfDocument(content=rdf, format="turtle")])
    assert "hummus.md" in report.created_notes

    # S3 store should have notes and context.jsonld under prefix
    keys = store.list()
    assert "hummus.md" in keys
    assert "context.jsonld" in keys

    # Query vault backed by S3
    triples = vault.triples()
    assert len(triples) >= 2

    export = vault.vault2rdf(source=True)
    assert "Hummus" in export.data_ttl


def test_s3_store_presign_put_and_get(s3_bucket: str) -> None:
    store = S3Store(bucket=s3_bucket, prefix="myvault/")

    put_url = store.presign_put("masters/video.mp4", expires_seconds=300)
    get_url = store.presign_get("masters/video.mp4", expires_seconds=300)

    assert ("X-Amz-Expires=300" in put_url) or ("Expires=" in put_url)
    assert ("X-Amz-Expires=300" in get_url) or ("Expires=" in get_url)
    assert "/myvault/masters/video.mp4" in put_url
    assert "/myvault/masters/video.mp4" in get_url
