#!/usr/bin/env python3
"""rdf_to_vault.py — Ingest RDF into a Vault-LD vault (the reverse of vault_to_rdf.py).

Reads one or more RDF files (Turtle, N-Triples, JSON-LD, ...) and projects every
subject into the vault format per SPEC §5.5: one subject ⇒ one Markdown note whose
YAML frontmatter carries the subject's triples, resolved through the vault's
composed context. Together with vault_to_rdf.py this closes the roundtrip of
SPEC §5.6: RDF ⇄ vault format, with neither side privileged.

Placement follows SPEC §5.1 — schema subjects land under Ontologies/ and
Vocabularies/ (grouped by the namespace their IRI is minted in), instances land
in plain folders. When a note for a subject already exists anywhere in the
vault it is updated *in place*, so the vault's structure survives regeneration.

Fidelity rules honoured (SPEC §5.5, §5.6, §6):
  - existing bodies are never clobbered; a freshly ingested note starts with an
    empty body,
  - frontmatter keys that are not context-mapped (tags, aliases, ...) belong to
    the Markdown face and are preserved,
  - a predicate with no short name in the context gets one coined from its
    localname and *added to the context* (the ontology's own context.jsonld
    when the predicate lives in that namespace, the root context otherwise),
  - anything the vault format cannot express (blank nodes, language tags,
    datatype mismatches) is flagged as a warning, never dropped silently,
  - a note whose frontmatter would not change is not rewritten at all.

Usage:
    python scripts/rdf_to_vault.py VAULT schema.ttl data.ttl
    python scripts/rdf_to_vault.py NewVault graph.ttl            # no context: one is synthesized
    python scripts/rdf_to_vault.py VAULT g.ttl --context other/context.jsonld --data-ns https://example.org/data/
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

import yaml
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF
from rdflib.term import Node

from oxivault.errors import ConversionError, ObjectReadLimitError

# Vault-LD's own tiny vocabulary (SPEC §5.4 step 7): vld:path is its only
# term — a plain string-valued property carrying a context-relative file path.
VLD_PATH = URIRef("https://github.com/The-Knowledge-Graph-Guys/vault-ld#path")

from oxivault._reference.vault_ld.vault_to_rdf import (
    EXPECTED_CLASS,
    EXPECTED_CONCEPT,
    EXPECTED_ONTOLOGY,
    EXPECTED_PROPERTY,
    EXPECTED_SCHEME,
    MAX_CONTEXT_BYTES,
    OWL,
    RDFS,
    SKOS,
    Context,
    canonical_keywords,
    context_base,
    governing,
    iri_safe,
    load_context,
    locate,
    parse_frontmatter,
    read_json_document,
    safe_relative_ref,
    within_root,
)

XSD = "http://www.w3.org/2001/XMLSchema#"
SKOS_IN_SCHEME = URIRef(SKOS + "inScheme")

# Literal datatypes YAML can carry natively without a context coercion: the
# forward direction re-infers the same datatype from the parsed YAML value.
NATIVE_SAFE = {
    XSD + "integer",
    XSD + "boolean",
    XSD + "double",
    XSD + "date",
    XSD + "dateTime",
}


class Raw(str):
    """A scalar emitted into YAML verbatim, unquoted (bare dates, dateTimes)."""


def core_context(data_ns: str) -> dict:
    """The cross-cutting core for a synthesized root context (SPEC Appendix A)."""
    return {
        "@base": data_ns,
        "type": "@type",
        "id": "@id",
        "owl": OWL,
        "rdfs": RDFS,
        "skos": SKOS,
        "xsd": XSD,
        "label": "rdfs:label",
        "comment": "rdfs:comment",
        "seeAlso": {"@id": "rdfs:seeAlso", "@type": "@id"},
        "isDefinedBy": {"@id": "rdfs:isDefinedBy", "@type": "@id"},
        "prefLabel": "skos:prefLabel",
        "altLabel": "skos:altLabel",
        "definition": "skos:definition",
        "scopeNote": "skos:scopeNote",
        "subClassOf": {"@id": "rdfs:subClassOf", "@type": "@id", "@container": "@set"},
        "subPropertyOf": {"@id": "rdfs:subPropertyOf", "@type": "@id", "@container": "@set"},
        "broader": {"@id": "skos:broader", "@type": "@id"},
        "narrower": {"@id": "skos:narrower", "@type": "@id"},
        "inScheme": {"@id": "skos:inScheme", "@type": "@id"},
        "topConceptOf": {"@id": "skos:topConceptOf", "@type": "@id"},
        "hasTopConcept": {"@id": "skos:hasTopConcept", "@type": "@id"},
        "domain": {"@id": "rdfs:domain", "@type": "@id"},
        "range": {"@id": "rdfs:range", "@type": "@id"},
        "equivalentClass": {"@id": "owl:equivalentClass", "@type": "@id"},
        "inverseOf": {"@id": "owl:inverseOf", "@type": "@id"},
    }


# ---------------------------------------------------------------------------
# IRI helpers
# ---------------------------------------------------------------------------

def split_iri(iri: str) -> tuple[str, str]:
    """Split an IRI into (namespace, localname) at the last '#' or '/'."""
    i = max(iri.rfind("#"), iri.rfind("/"))
    if i == -1 or i == len(iri) - 1:
        return iri, ""
    return iri[: i + 1], iri[i + 1:]


def sanitize_stem(local: str) -> str:
    """Make a localname safe as a file stem, percent-decoding first (SPEC §4.5,
    §5.5): the forward direction minted `Red Lentil Soup.md` as
    `Red%20Lentil%20Soup`, so decoding restores the file name that mints back
    to the identical IRI — no explicit @id pin needed.

    The localname is untrusted (it comes from a foreign IRI) and the stem
    becomes a path segment, so anything with path meaning is neutralised:
    separators and control characters are replaced, and a leading-dot name
    ('.', '..', '.hidden') is stripped of its dots — '..' is a path step, not
    a name, and it must never survive into a joinpath.
    """
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", unquote(local))
    stem = stem.lstrip(".").strip()
    return stem or "unnamed"


def derive_folder_name(ns: str) -> str:
    """Fall back to a folder name for a namespace with no ontology/scheme subject."""
    for seg in reversed(re.split(r"[/#]+", ns.rstrip("/#"))):
        if re.search(r"[A-Za-z]", seg):
            clean = re.sub(r"[^\w\-]", "", seg)
            return clean[:1].upper() + clean[1:]
    return "Imported"


def pluralize(name: str) -> str:
    return name if name.endswith("s") else name + "s"


# ---------------------------------------------------------------------------
# Context files: read/extend/write, preserving what is already there
# ---------------------------------------------------------------------------

class ContextEditor:
    """Edits one context.jsonld document, writing back only if changed."""

    def __init__(self, path: Path, initial: dict | None = None, *, max_context_bytes: int = MAX_CONTEXT_BYTES):
        self.path = path
        self.max_context_bytes = max_context_bytes
        self.doc: dict
        if path.exists():
            # fail closed on an unusable existing context: editing means
            # writing it back, and a parse failure must not become an
            # overwrite of a file the user can still inspect and repair
            problems: list[str] = []
            doc = read_json_document(path, problems, max_context_bytes)
            if doc is None:
                raise ConversionError(f"Invalid context {path.name}: {problems[-1]}")
            self.doc = doc
            self.dirty = False
        else:
            self.doc = {"@context": initial if initial is not None else {}}
            self.dirty = True

    def _first_dict(self) -> dict:
        ctx = self.doc.setdefault("@context", {})
        if isinstance(ctx, dict):
            return ctx
        for entry in ctx:
            if isinstance(entry, dict):
                return entry
        entry = {}
        ctx.insert(0, entry)
        return entry

    def has(self, name: str) -> bool:
        ctx = self.doc.get("@context")
        entries = ctx if isinstance(ctx, list) else [ctx]
        return any(isinstance(e, dict) and name in e for e in entries)

    def add_term(self, name: str, tdef: dict) -> None:
        if self.has(name):
            return
        # single-@id terms use the compact string form ("label": "rdfs:label")
        self._first_dict()[name] = tdef["@id"] if list(tdef) == ["@id"] else tdef
        self.dirty = True

    def add_prefix(self, prefix: str, ns: str) -> None:
        if not self.has(prefix):
            self._first_dict()[prefix] = ns
            self.dirty = True

    def add_reference(self, ref: str) -> None:
        ctx = self.doc.setdefault("@context", {})
        if isinstance(ctx, dict):
            entries: list[dict | str] = [ctx]
        elif isinstance(ctx, list):
            entries = ctx
        else:
            raise ConversionError(f"Invalid @context in {self.path.name}: expected object or array")
        if ref not in entries:
            entries.append(ref)
            self.doc["@context"] = entries
            self.dirty = True

    def save(self) -> bool:
        if not self.dirty:
            return False
        content = (json.dumps(self.doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if len(content) > self.max_context_bytes:
            raise ObjectReadLimitError(self.path.name, self.max_context_bytes, len(content))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(content)
        self.dirty = False
        return True


# ---------------------------------------------------------------------------
# YAML frontmatter emission, styled like the notes a human would write
# ---------------------------------------------------------------------------

PLAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-'./:]*$")


def plain_safe(s: str) -> bool:
    """True when a string can be written as a bare YAML scalar (also inside
    a flow list) and re-parse to exactly itself.
    """
    if not PLAIN_RE.fullmatch(s) or ": " in s or " #" in s or s.endswith(" "):
        return False
    try:
        return yaml.safe_load(s) == s
    except yaml.YAMLError:
        return False


def emit_scalar(v) -> str:
    if isinstance(v, Raw):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):  # preserved non-context keys may hold structures
        return yaml.safe_dump(v, default_flow_style=True, sort_keys=False).strip()
    s = str(v)
    return s if plain_safe(s) else json.dumps(s, ensure_ascii=False)


def emit_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for key, val in fm.items():
        # a key is written bare only when it re-parses to exactly itself;
        # everything else — @keywords, and coined term names carrying hostile
        # characters from a foreign IRI (newlines, ': ') — is JSON-quoted so
        # untrusted content can never inject frontmatter lines
        k = key if plain_safe(key) else json.dumps(key)
        if isinstance(val, list):
            lines.append(f"{k}: [ " + ", ".join(emit_scalar(v) for v in val) + " ]")
        else:
            lines.append(f"{k}: {emit_scalar(val)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def norm(v):
    """Normalize a frontmatter value for semantic comparison (list-of-one ==
    scalar, dates == their ISO strings, order-insensitive lists).
    """
    if isinstance(v, list):
        n = [norm(x) for x in v]
        return n[0] if len(n) == 1 else sorted(n, key=str)
    if isinstance(v, Raw):
        try:
            parsed = yaml.safe_load(str(v))
        except yaml.YAMLError:
            return str(v)
        return norm(parsed) if not isinstance(parsed, (str, type(None))) else str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


# ---------------------------------------------------------------------------
# The ingest
# ---------------------------------------------------------------------------

def governing_base(path: Path, vault: Path, root_base: str,
                   warnings: list[str], max_context_bytes: int = MAX_CONTEXT_BYTES) -> tuple[str, Path]:
    """The (base, folder) of the nearest context.jsonld at or above a note
    (SPEC §4.5). Falls back to the vault base when none is on disk yet.
    """
    d = path.parent
    while d != vault and not (d / "context.jsonld").exists():
        d = d.parent
    if (d / "context.jsonld").exists():
        base = context_base(d / "context.jsonld", warnings, max_context_bytes)
        if base:
            return base, d
    return root_base, vault


def minted_iri(path: Path, vault: Path, root_base: str,
               warnings: list[str], max_context_bytes: int = MAX_CONTEXT_BYTES) -> tuple[str, str]:
    """(minted IRI, governing base) for a note, exactly as the forward
    direction mints identity (SPEC §4.5): the file name alone under the
    governing @base — an ontology's/vocabulary's for schema notes, the
    nearest data context's otherwise. Folders never enter the IRI.
    """
    layer, _ = locate(path, vault)
    if layer == "data":
        base, _folder = governing_base(path, vault, root_base, warnings, max_context_bytes)
        return base + iri_safe(path.stem), base
    gov, _name = governing(path, vault)
    if gov is None:
        raise ConversionError(f"No governing schema context: {path.relative_to(vault)}")
    base = context_base(gov.parent / "context.jsonld", warnings, max_context_bytes) or ""
    return base + iri_safe(path.stem), base


def classify(type_iris: set[str]) -> str:
    if type_iris & EXPECTED_ONTOLOGY:
        return "ontology"
    if type_iris & EXPECTED_SCHEME:
        return "scheme"
    if type_iris & EXPECTED_CLASS:
        return "class"
    if type_iris & EXPECTED_PROPERTY:
        return "property"
    if type_iris & EXPECTED_CONCEPT:
        return "concept"
    return "instance"


def scan_existing_notes(vault: Path, ctx: Context, root_base: str,
                        warnings: list[str], max_context_bytes: int = MAX_CONTEXT_BYTES) -> dict[str, Path]:
    """Map every existing note's subject IRI to its path, minting identity
    exactly as the forward direction does (SPEC §4.5).

    Notes are keyed by IRI, not by name: two notes may legitimately share a
    file name (links to them are emitted path-qualified, SPEC §4.4.1), but two
    notes identifying the same subject would race for one update — the first
    wins and the duplicate is flagged.
    """
    by_iri: dict[str, Path] = {}
    if not vault.exists():
        return by_iri
    vault_root = vault.resolve()
    for path in sorted(vault.rglob("*.md")):
        # a note reached through a symlink is not part of the vault: updating
        # it in place would write outside the tree being ingested into
        if path.is_symlink() or not within_root(path, vault_root):
            warnings.append(f"{path.relative_to(vault)} is a symlink or resolves "
                            f"outside the vault — skipped")
            continue
        fm = canonical_keywords(parse_frontmatter(path) or {}, ctx)
        minted, base = minted_iri(path, vault, root_base, warnings, max_context_bytes)
        if "@id" in fm:
            token = str(fm["@id"]).strip()
            iri = token if token.startswith(("http://", "https://")) else base + token
        else:
            iri = minted
        if iri in by_iri:
            warnings.append(f"notes {by_iri[iri]} and {path} both identify <{iri}> "
                            f"— updates go to the former")
            continue
        by_iri[iri] = path
    return by_iri


def rdf2vault_reference(
    vault: Path,
    graph: Graph,
    *,
    context_path: Path | None = None,
    nest: bool = False,
    data_ns: str = "https://example.org/data/",
    max_context_bytes: int = MAX_CONTEXT_BYTES,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """In-process callable version of rdf_to_vault.

    Returns:
        tuple of (created_notes, updated_notes, unchanged_notes, written_contexts, warnings)
    """
    g = graph
    warnings: list[str] = []

    # ---- Resolve the context: given, found in the vault, or synthesized.
    root_target = vault / "context.jsonld"
    context_path = context_path or root_target
    if context_path.exists():
        ctx = load_context(context_path, warnings, max_context_bytes)
        if context_path.resolve() != root_target.resolve() and not root_target.exists():
            doc = read_json_document(context_path, warnings, max_context_bytes)
            if doc is None:
                raise ConversionError(f"Unable to read context document: {context_path.name}")
            entries = doc.get("@context")
            for ref in entries if isinstance(entries, list) else []:
                if isinstance(ref, str) and not ref.startswith(("http://", "https://")):
                    if not safe_relative_ref(ref):
                        warnings.append(
                            f"context reference '{ref}' is not a plain relative path — not copied"
                        )
                        continue
                    src = context_path.parent / ref
                    dst = vault / ref
                    if not (within_root(src, context_path.parent) and within_root(dst, vault)):
                        warnings.append(f"context reference '{ref}' escapes its tree — not copied")
                        continue
                    if src.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            root_editor = ContextEditor(root_target, initial={}, max_context_bytes=max_context_bytes)
            root_editor.doc, root_editor.dirty = doc, True
        else:
            root_editor = ContextEditor(root_target, max_context_bytes=max_context_bytes)
    else:
        core = core_context(data_ns)
        ctx = Context(core)
        root_editor = ContextEditor(root_target, initial=dict(core), max_context_bytes=max_context_bytes)
        warnings.append(f"no context found — synthesizing {root_target}")

    root_base = ctx.base or data_ns

    pred_to_term: dict[str, tuple[str, dict]] = {}
    for name, tdef in ctx.terms.items():
        pred_to_term.setdefault(ctx.expand_curie(tdef["@id"]), (name, tdef))

    graph_prefixes = {p: str(n) for p, n in g.namespaces() if p}
    all_prefixes = {**graph_prefixes, **ctx.prefixes}
    used_prefixes: dict[str, str] = {}

    def curie(iri: str) -> str:
        best = None
        for pfx, ns in all_prefixes.items():
            if (
                iri.startswith(ns)
                and len(iri) > len(ns)
                and (best is None or len(ns) > len(best[1]))
            ):
                best = (pfx, ns)
        if best:
            local = iri[len(best[1]) :]
            if re.fullmatch(r"[A-Za-z_][\w.\-]*", local):
                used_prefixes[best[0]] = best[1]
                return f"{best[0]}:{local}"
        return iri

    bnode_subjects = {s for s in g.subjects() if isinstance(s, BNode)}
    if bnode_subjects:
        warnings.append(
            f"{len(bnode_subjects)} blank-node subject(s) skipped — "
            f"the vault format has no representation for anonymous resources"
        )
    subjects = sorted({s for s in g.subjects() if isinstance(s, URIRef)})
    types_of = {
        s: {str(o) for o in g.objects(s, RDF.type) if isinstance(o, URIRef)} for s in subjects
    }
    kind_of = {s: classify(types_of[s]) for s in subjects}

    path_hint: dict[str, str] = {}
    for s, o in g.subject_objects(VLD_PATH):
        if isinstance(s, URIRef) and isinstance(o, Literal) and str(o).endswith(".md"):
            if not safe_relative_ref(str(o)):
                warnings.append(f"vld:path '{o}' for <{s}> is not a plain relative path — hint refused")
                continue
            path_hint[str(s)] = str(o)

    ns_to_folder: dict[str, tuple[str, str]] = {}
    for kind_dir in ("Ontologies", "Vocabularies"):
        d = vault / kind_dir
        if d.exists():
            for sub in sorted(p for p in d.iterdir() if p.is_dir()):
                base = context_base(sub / "context.jsonld", warnings, max_context_bytes)
                if base:
                    ns_to_folder.setdefault(base, (kind_dir, sub.name))

    def register(subj: URIRef, kind_dir: str) -> None:
        ns, local = split_iri(str(subj))
        name = sanitize_stem(local) if local else derive_folder_name(ns)
        for candidate in (ns, str(subj) + "#", str(subj) + "/"):
            ns_to_folder.setdefault(candidate, (kind_dir, name))

    for s in subjects:
        if kind_of[s] == "ontology":
            register(s, "Ontologies")
    for s in subjects:
        if kind_of[s] == "scheme":
            register(s, "Vocabularies")

    def folder_for(s: URIRef) -> tuple[str, str]:
        ns, _ = split_iri(str(s))
        if ns in ns_to_folder:
            return ns_to_folder[ns]
        if kind_of[s] == "concept":
            scheme = g.value(s, SKOS_IN_SCHEME)
            if isinstance(scheme, URIRef):
                _, sl = split_iri(str(scheme))
                folder = ("Vocabularies", sanitize_stem(sl) if sl else derive_folder_name(ns))
            else:
                folder = ("Vocabularies", derive_folder_name(ns))
        else:
            folder = ("Ontologies", derive_folder_name(ns))
        warnings.append(
            f"namespace {ns} has no ontology/scheme subject — grouping under {folder[0]}/{folder[1]}/"
        )
        ns_to_folder[ns] = folder
        return folder

    existing_by_iri = scan_existing_notes(vault, ctx, root_base, warnings, max_context_bytes)
    existing_path_iri = {p: i for i, p in existing_by_iri.items()}

    def context_folder_for(iri: str) -> Path:
        best_len, best = (len(root_base), vault) if iri.startswith(root_base) else (-1, vault)
        for b, (kd, name) in ns_to_folder.items():
            if iri.startswith(b) and len(b) > best_len:
                best_len, best = len(b), vault / kd / name
        return best

    vault_root = vault.resolve()

    note_path: dict[str, Path] = {}
    note_stem: dict[str, str] = {}
    folder_members: dict[tuple[str, str], list[str]] = {}
    claimed: dict[Path, str] = {}

    def taken_by_other(path: Path, iri: str) -> bool:
        if path in claimed:
            return claimed[path] != iri
        if path.exists():
            return existing_path_iri.get(path, iri) != iri
        return False

    def free_path(path: Path, iri: str) -> Path:
        base, i = path, 2
        while taken_by_other(path, iri):
            path = base.with_name(f"{base.stem}-{i}{base.suffix}")
            i += 1
        if path != base:
            warnings.append(
                f"file collision: {base.relative_to(vault)} already belongs to another subject — "
                f"{iri} stored as '{path.stem}' with an explicit @id"
            )
        return path

    RDFS_SUBCLASS = URIRef(RDFS + "subClassOf")
    SKOS_BROADER = URIRef(SKOS + "broader")
    SKOS_TOPCONCEPT = URIRef(SKOS + "topConceptOf")

    def parent_chain(s: URIRef, rel_pred: URIRef, ns: str) -> list[str]:
        segs: list[str] = []
        seen = {s}
        cur = s
        while True:
            parents = [
                o
                for o in g.objects(cur, rel_pred)
                if isinstance(o, URIRef) and o in types_of and split_iri(str(o))[0] == ns
            ]
            if len(parents) != 1 or parents[0] in seen:
                break
            cur = parents[0]
            seen.add(cur)
            segs.append(sanitize_stem(split_iri(str(cur))[1]))
        return list(reversed(segs))

    for s in subjects:
        iri = str(s)
        kind = kind_of[s]
        ns, local = split_iri(iri)
        stem = sanitize_stem(local) if local else derive_folder_name(ns)

        hinted: Path | None = None
        if iri in path_hint:
            candidate = context_folder_for(iri) / path_hint[iri]
            if within_root(candidate, vault_root):
                hinted = candidate
            else:
                warnings.append(
                    f"vld:path '{path_hint[iri]}' for <{iri}> resolves outside the vault — hint refused"
                )
                del path_hint[iri]

        if iri in existing_by_iri:
            path = existing_by_iri[iri]
            folder = None
        elif hinted is not None:
            path = free_path(hinted, iri)
            folder = None
        elif (
            kind == "instance"
            and iri.startswith(root_base)
            and "/" not in iri[len(root_base) :]
            and len(iri) > len(root_base)
        ):
            stem = sanitize_stem(iri[len(root_base) :])
            path = free_path(vault / f"{stem}.md", iri)
            folder = None
        elif kind == "instance":
            type_stems = sorted(split_iri(t)[1] for t in types_of[s] if split_iri(t)[1])
            folder_name = pluralize(sanitize_stem(type_stems[0])) if type_stems else "Resources"
            path = free_path(vault / folder_name / f"{stem}.md", iri)
            folder = None
        else:
            kind_dir, name = folder_for(s)
            if kind in ("ontology", "scheme"):
                path = vault / kind_dir / name / f"{name}.md"
            elif kind == "class":
                chain = parent_chain(s, RDFS_SUBCLASS, ns) if nest else []
                path = vault.joinpath(kind_dir, name, "Classes", *chain, f"{stem}.md")
            elif kind == "property":
                path = vault / kind_dir / name / "Properties" / f"{stem}.md"
            elif kind == "concept":
                chain = (
                    []
                    if not nest or (s, SKOS_TOPCONCEPT, None) in g
                    else parent_chain(s, SKOS_BROADER, ns)
                )
                path = vault.joinpath(kind_dir, name, *chain, f"{stem}.md")
            else:
                path = vault / kind_dir / name / f"{stem}.md"
            path = free_path(path, iri)
            folder = (kind_dir, name)

        claimed[path] = iri
        if kind != "instance" and folder is not None:
            folder_members.setdefault(folder, []).append(ns)
        note_path[iri] = path
        note_stem[iri] = path.stem
        if not types_of[s]:
            warnings.append(
                f"{path.name}: subject {iri} has no rdf:type — the note will be skipped by a forward export"
            )

    note_by_iri = {iri: p.stem for iri, p in existing_by_iri.items()}
    note_by_iri.update(note_stem)
    path_by_iri = dict(existing_by_iri)
    path_by_iri.update(note_path)

    stem_iris: dict[str, set[str]] = {}
    for iri_, stem_ in note_by_iri.items():
        stem_iris.setdefault(stem_, set()).add(iri_)

    def link_for(iri: str) -> str | None:
        stem = note_by_iri.get(iri)
        if stem is None:
            return None
        if len(stem_iris[stem]) > 1 and iri in path_by_iri:
            rel = path_by_iri[iri].relative_to(vault).with_suffix("").as_posix()
            warnings.append(
                f"note name '{stem}' is ambiguous — linking to it path-qualified as [[{rel}]] (SPEC §4.4.1)"
            )
            return f"[[{rel}]]"
        return f"[[{stem}]]"

    folder_editors: dict[str, ContextEditor] = {}
    written_contexts: list[Path] = []
    for (kind_dir, name), namespaces in sorted(folder_members.items()):
        cpath = vault / kind_dir / name / "context.jsonld"
        base = Counter(namespaces).most_common(1)[0][0]
        if cpath.exists():
            editor = ContextEditor(cpath, max_context_bytes=max_context_bytes)
            declared = context_base(cpath, warnings, max_context_bytes)
        else:
            prefix = next((p for p, n in graph_prefixes.items() if n == base), None)
            editor = ContextEditor(cpath, initial={"@base": base, prefix or name.lower(): base},
                                   max_context_bytes=max_context_bytes)
            root_editor.add_reference(f"{kind_dir}/{name}/context.jsonld")
            declared = base
            if editor.save():
                written_contexts.append(editor.path)
        folder_editors[declared or base] = editor
        folder_editors.setdefault(base, editor)

    def editor_for_ns(ns: str) -> ContextEditor:
        if ns in folder_editors:
            return folder_editors[ns]
        if ns in ns_to_folder:
            kind_dir, name = ns_to_folder[ns]
            cpath = vault / kind_dir / name / "context.jsonld"
            if cpath.exists():
                folder_editors[ns] = ContextEditor(cpath, max_context_bytes=max_context_bytes)
                return folder_editors[ns]
        return root_editor

    def term_for(pred: Node, objs: list) -> tuple[str, dict]:
        iri = str(pred)
        if iri in pred_to_term:
            return pred_to_term[iri]
        ns, local = split_iri(iri)
        name = local or "property"
        base_name, i = name, 2
        while name in ctx.terms:
            name, i = f"{base_name}{i}", i + 1
        tdef: dict = {"@id": curie(iri)}
        if objs and all(isinstance(o, URIRef) for o in objs):
            tdef["@type"] = "@id"
        else:
            dts = {str(o.datatype) for o in objs if isinstance(o, Literal) and o.datatype}
            if len(dts) == 1 and (dt := dts.pop()) not in NATIVE_SAFE and dt != XSD + "string":
                tdef["@type"] = curie(dt)
        target = editor_for_ns(ns)
        target.add_term(name, tdef)
        ctx.terms[name] = tdef
        pred_to_term[iri] = (name, tdef)
        warnings.append(
            f"predicate {curie(iri)} not in context — coined term '{name}' and added it to {target.path}"
        )
        return name, tdef

    def render_value(obj, coercion) -> object | None:
        if isinstance(obj, BNode):
            warnings.append("blank-node object skipped — not expressible as a wiki link")
            return None
        if isinstance(obj, URIRef):
            iri = str(obj)
            if coercion != "@id":
                warnings.append(
                    f"IRI object {curie(iri)} through a term without @id coercion — will export as a string literal"
                )
            return link_for(iri) or curie(iri)
        lit: Literal = obj
        if lit.language:
            warnings.append(
                f"language tag @{lit.language} on '{lit}' dropped — not expressible in frontmatter"
            )
            return str(lit)
        dt = str(lit.datatype) if lit.datatype else None
        if coercion == "@id":
            warnings.append(f"literal '{lit}' through @id-coerced term — will export as an IRI")
            return str(lit)
        if coercion is not None:
            cd = ctx.expand_curie(coercion)
            if dt is not None and cd != dt:
                warnings.append(
                    f"literal '{lit}' typed {curie(dt)} but term coerces to {coercion} — retyped on export"
                )
            return native(lit)
        if dt is None or dt == XSD + "string":
            return str(lit)
        if dt in NATIVE_SAFE:
            return native(lit)
        warnings.append(
            f"typed literal '{lit}' ({curie(dt)}) on an uncoerced term — datatype lost on export"
        )
        return str(lit)

    def native(lit: Literal):
        v = lit.toPython()
        if isinstance(v, bool) or type(v) is int or type(v) is float:
            return v
        if isinstance(v, (date, datetime)):
            return Raw(str(lit))
        return str(lit)

    canon = {k: i for i, k in enumerate(["@id", "@type", *ctx.terms])}
    created_notes: list[str] = []
    updated_notes: list[str] = []
    unchanged_notes: list[str] = []

    alias_of = {kw: alias for alias, kw in ctx.aliases.items()}

    def spelled(fm: dict) -> dict:
        return {alias_of.get(k, k): v for k, v in fm.items()}

    for s in subjects:
        iri = str(s)
        path, stem = note_path[iri], note_stem[iri]

        if not within_root(path, vault_root):
            warnings.append(
                f"note path for <{iri}> resolves outside the vault — subject skipped"
            )
            continue

        old_text = path.read_text(encoding="utf-8") if path.exists() else ""
        old_fm = parse_frontmatter(path) if path.exists() else None
        if old_fm is None and old_text:
            body = old_text if old_text.startswith("\n") else "\n" + old_text
        elif old_text:
            body = old_text.split("---", 2)[2]
        else:
            body = "\n"
        old_fm = canonical_keywords(old_fm or {}, ctx)

        minted, base = minted_iri(path, vault, root_base, warnings, max_context_bytes)

        new_fm: dict = {}
        if minted != iri:
            new_fm["@id"] = iri
        elif "@id" in old_fm and str(old_fm["@id"]).strip() == iri:
            new_fm["@id"] = old_fm["@id"]

        type_vals = sorted(link_for(t) or curie(t) for t in types_of[s])
        if type_vals:
            new_fm["@type"] = type_vals[0] if len(type_vals) == 1 else type_vals

        preds = sorted(set(g.predicates(s)) - {RDF.type}, key=str)
        rendered: list[tuple[str, dict, object]] = []
        for p in preds:
            objs = sorted(g.objects(s, p), key=str)
            if p == VLD_PATH and iri in path_hint:
                objs = [o for o in objs if str(o) != path_hint[iri]]
                if not objs:
                    continue
            name, tdef = term_for(p, objs)
            vals = [v for o in objs if (v := render_value(o, tdef.get("@type"))) is not None]
            if not vals:
                continue
            as_list = len(vals) > 1 or tdef.get("@container") == "@set"
            rendered.append((name, tdef, sorted(vals, key=str) if as_list else vals[0]))
        for name, _, val in sorted(rendered, key=lambda r: canon.get(r[0], len(canon))):
            new_fm[name] = val

        final: dict = {}
        for k, v in old_fm.items():
            if k in new_fm:
                final[k] = v if norm(v) == norm(new_fm[k]) else new_fm[k]
            elif not k.startswith("@") and k not in ctx.terms:
                final[k] = v
        for k, v in new_fm.items():
            final.setdefault(k, v)

        rel_key = path.relative_to(vault).as_posix()
        if path.exists() and final == old_fm:
            unchanged_notes.append(rel_key)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(emit_frontmatter(spelled(final)) + body, encoding="utf-8")
        if old_text:
            updated_notes.append(rel_key)
        else:
            created_notes.append(rel_key)

    for pfx, ns in sorted(used_prefixes.items()):
        if pfx not in ctx.prefixes:
            root_editor.add_prefix(pfx, ns)

    for editor in dict.fromkeys([root_editor, *folder_editors.values()]):
        if editor.save() and editor.path not in written_contexts:
            written_contexts.append(editor.path)

    written_ctx_rels = [p.relative_to(vault).as_posix() for p in dict.fromkeys(written_contexts)]
    return (
        created_notes,
        updated_notes,
        unchanged_notes,
        written_ctx_rels,
        list(dict.fromkeys(warnings)),
    )
