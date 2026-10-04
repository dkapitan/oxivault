#!/usr/bin/env python3
"""
vault_to_rdf.py — Project a Vault-LD vault to RDF, split by namespace.

Walks a vault of Markdown notes (per the Vault-LD SPEC), reads each note's
YAML frontmatter as YAML-LD through the shared context.jsonld, and emits two
Turtle files:

    schema.ttl   the schema layer  — classes, properties, ontologies, concepts,
                 each minted from its file name under its ontology's own @base
    data.ttl     the instance layer — typed notes (recipes, ingredients, ...),
                 each minted from its context-relative file path under the
                 governing context's @base (SPEC §4.5)

A note is schema-layer when its `@type` is a CURIE (owl:Class, skos:Concept, ...)
and instance-layer when its `@type` is a wiki link ("[[Recipe]]"), matching the
distinction drawn in SPEC §4.6.

Usage:
    python scripts/vault_to_rdf.py "Vault-LD Example"
    python scripts/vault_to_rdf.py VAULT --context VAULT/context.jsonld --out-dir build
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml
from urllib.parse import quote

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

from oxivault.errors import ConversionError, ObjectReadLimitError

# Vault-LD's own tiny vocabulary (SPEC §5.4 step 7): vld:path is its only
# term — a plain string-valued property carrying a context-relative file path.
VLD = "https://github.com/The-Knowledge-Graph-Guys/vault-ld#"
VLD_PATH = URIRef(VLD + "path")

# Host-editor keys are affordances of the editing surface, not triples
# (SPEC §4.3): when unmapped, never emitted and never warned about. A context
# mapping promotes one to an ordinary term, exported like any other.
HOST_KEYS = {"tags", "aliases", "cssclasses"}


def iri_safe(name: str) -> str:
    """Percent-encode characters not legal in an IRI local part (SPEC §4.5)."""
    return quote(name, safe="")


# ---------------------------------------------------------------------------
# Trust boundary: a vault may be cloned from anywhere and an RDF file may come
# from a foreign ontology, so every path read from either is untrusted. Any
# path assembled from vault/RDF content must stay inside the tree it belongs
# to — these helpers are the single containment check both tools share.
# ---------------------------------------------------------------------------

def within_root(candidate: Path, root: Path) -> bool:
    """True when `candidate` resolves to a location inside `root`."""
    return candidate.resolve().is_relative_to(root.resolve())


def safe_relative_ref(ref: str) -> bool:
    """True when a path string from untrusted content is a plain relative
    path that cannot climb out of its base directory: no absolute form (POSIX
    or Windows), no backslashes, no '..' segments."""
    if "\\" in ref or "\x00" in ref:
        return False
    p = PurePosixPath(ref)
    if p.is_absolute() or PureWindowsPath(ref).is_absolute():
        return False
    return ".." not in p.parts

# ---------------------------------------------------------------------------
# Folder structure decides the layer (SPEC §3, §5.1): the schema layer lives
# under Ontologies/ and Vocabularies/, everything else is the instance layer.
#
# The acceptable-type sets below are NOT used to classify — they are an error
# bound. Given a note's location we know what kind of resource it ought to be,
# so if its @type isn't one of the expected types we emit a warning rather than
# silently mis-modelling it.
# ---------------------------------------------------------------------------
OWL = "http://www.w3.org/2002/07/owl#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SKOS = "http://www.w3.org/2004/02/skos/core#"

EXPECTED_CLASS = {OWL + "Class", RDFS + "Class"}
EXPECTED_PROPERTY = {
    OWL + "ObjectProperty",
    OWL + "DatatypeProperty",
    OWL + "AnnotationProperty",
    RDF_NS + "Property",
}
EXPECTED_ONTOLOGY = {OWL + "Ontology"}
EXPECTED_SCHEME = {SKOS + "ConceptScheme"}
EXPECTED_CONCEPT = {SKOS + "Concept"}


# Context documents are untrusted content like everything else in a cloned
# vault: cap their size (the cap must bind *before* the bytes are in memory)
# and turn parse failures into warnings instead of tracebacks.
MAX_CONTEXT_BYTES = 4 << 20  # 4 MiB


def read_json_document(
    path: Path, warnings: list[str], max_context_bytes: int = MAX_CONTEXT_BYTES
) -> dict | None:
    """Read a JSON document from untrusted content: size-capped, and any
    failure (unreadable, oversized, malformed, not an object) is reported as
    a warning and returns None rather than raising."""
    try:
        with path.open("rb") as stream:
            content = stream.read(max_context_bytes + 1)
        if len(content) > max_context_bytes:
            raise ObjectReadLimitError(path.name, max_context_bytes, len(content))
        doc = json.loads(content)
    except (OSError, ValueError, RecursionError) as e:
        warnings.append(f"{path}: unreadable JSON document "
                        f"({e.__class__.__name__}) — skipped")
        return None
    if not isinstance(doc, dict):
        warnings.append(f"{path}: JSON document is not an object — skipped")
        return None
    return doc


def _def_form(d):
    """A term definition in its expanded dict form, so the compact string form
    compares equal to its equivalent object ("rdfs:label" == {"@id": "rdfs:label"})."""
    return d if isinstance(d, dict) else {"@id": d}


def merge_context(node, base_dir: Path, seen: set[Path], warnings: list[str],
                  root: Path | None = None, max_context_bytes: int = MAX_CONTEXT_BYTES) -> dict:
    """Resolve a JSON-LD `@context` value into one flat mapping.

    Follows JSON-LD composition semantics: a context may be an inline object, a
    string reference to another context document, or an array of either, applied
    left-to-right with later entries overriding earlier ones. String references
    are resolved as file paths relative to the document that names them, so each
    ontology can ship its own self-contained context (as published ontologies
    do) and the root context simply lists the ones it composes.

    A later entry that redefines a term or prefix with a *different* definition
    is flagged (SPEC §4.2): legal JSON-LD, but across independently authored
    ontologies it is almost always an accidental collision. An identical
    re-declaration (self-contained contexts re-declaring common prefixes) is
    benign and stays silent.

    `root` is the containment boundary for string references: a reference that
    resolves outside it is refused (a cloned vault is untrusted content, so a
    context must not be able to read arbitrary files on the machine).
    """
    if root is None:
        root = base_dir
    merged: dict = {}
    if isinstance(node, dict):
        merged.update(node)
    elif isinstance(node, list):
        for entry in node:
            sub = merge_context(entry, base_dir, seen, warnings, root, max_context_bytes)
            src = entry if isinstance(entry, str) else "an inline context"
            for key, val in sub.items():
                if key.startswith("@") or key not in merged:
                    continue
                if _def_form(merged[key]) != _def_form(val):
                    warnings.append(f"context shadowing: '{key}' redefined with a "
                                    f"different definition by {src} — the later "
                                    f"definition wins (SPEC §4.2)")
            merged.update(sub)
    elif isinstance(node, str):
        if node.startswith("http://") or node.startswith("https://"):
            warnings.append(f"remote context not fetched: {node}")
            return merged
        if not safe_relative_ref(node):
            warnings.append(f"context reference refused (absolute or '..'): {node}")
            return merged
        ref = (base_dir / node).resolve()
        if not within_root(ref, root):
            warnings.append(f"context reference escapes its document tree: {node} — refused")
            return merged
        if ref in seen:
            return merged  # cycle guard
        if not ref.exists():
            warnings.append(f"referenced context not found: {node}")
            return merged
        seen.add(ref)
        doc = read_json_document(ref, warnings, max_context_bytes)
        if doc is None:
            return merged
        sub = merge_context(doc.get("@context"), ref.parent, seen, warnings, root, max_context_bytes)
        # A referenced context contributes vocabulary, not a new document base:
        # its @base scopes only its own ontology's subjects (read separately by
        # context_base), so it must not override the root's @base here.
        sub.pop("@base", None)
        merged.update(sub)
    return merged


def context_base(path: Path, warnings: list[str], max_context_bytes: int = MAX_CONTEXT_BYTES) -> str | None:
    """Return the `@base` an ontology/vocabulary context declares, or None.

    Read in isolation (not merged into the root) so each ontology keeps its own
    scoped base — the namespace its members are minted under.
    """
    if not path.exists():
        return None
    doc = read_json_document(path, warnings, max_context_bytes)
    if doc is None:
        return None
    mapping = merge_context(
        doc.get("@context"), path.parent, {path.resolve()}, warnings, max_context_bytes=max_context_bytes
    )
    return mapping.get("@base")


def load_context(path: Path, warnings: list[str], max_context_bytes: int = MAX_CONTEXT_BYTES) -> "Context":
    """Load a context document, composing any contexts it references. The
    root context is load-bearing (it decides where subjects mint), so an
    unusable one is a hard error, not a silent fallback."""
    doc = read_json_document(path, warnings, max_context_bytes)
    if doc is None:
        raise ConversionError(f"Invalid context {path.name}: {warnings[-1]}")
    mapping = merge_context(
        doc.get("@context"), path.parent, {path.resolve()}, warnings, max_context_bytes=max_context_bytes
    )
    return Context(mapping)


class Context:
    """A composed @context: prefix map, short-name term definitions, and any
    keyword aliases ("type": "@type", "id": "@id" — JSON-LD 1.1 keyword
    aliasing, SPEC §4.3)."""

    def __init__(self, ctx: dict):
        self.base = ctx.get("@base", "")
        self.prefixes: dict[str, str] = {}
        self.terms: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}   # alias name -> keyword ("type" -> "@type")
        for key, val in ctx.items():
            if key.startswith("@"):
                continue
            target = val.get("@id") if isinstance(val, dict) else val
            if target in ("@type", "@id"):
                self.aliases[key] = target
                continue
            if isinstance(val, str) and (val.startswith("http") or "#" in val or val.endswith("/")):
                # treat single-string namespace-looking values as prefixes,
                # and single-string IRI-mapped terms as terms too.
                if val.startswith("http") and (val.endswith("/") or val.endswith("#")):
                    self.prefixes[key] = val
                else:
                    self.terms[key] = {"@id": val}
            elif isinstance(val, str):
                self.terms[key] = {"@id": val}
            elif isinstance(val, dict):
                self.terms[key] = val

    def expand_curie(self, token: str) -> str:
        """Expand 'prefix:local' to a full IRI; pass full IRIs through."""
        token = token.strip()
        if token.startswith("http://") or token.startswith("https://"):
            return token
        if ":" in token:
            prefix, local = token.split(":", 1)
            if prefix in self.prefixes:
                return self.prefixes[prefix] + local
        return self.base + token


# Frontmatter is untrusted input (a vault may be cloned from anywhere), so it
# is parsed with a loader that refuses YAML aliases — alias expansion is a
# memory-amplification vector ("billion laughs") and frontmatter has no
# legitimate use for it — and capped in size so one hostile note cannot stall
# a whole-vault sweep.
MAX_FRONTMATTER_BYTES = 1 << 20  # 1 MiB


class FrontmatterLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not allowed in frontmatter")
        return super().compose_node(parent, index)


def parse_frontmatter(path: Path) -> dict | None:
    """Return the YAML frontmatter of a Markdown note as a dict, or None.
    Oversized or malformed frontmatter is skipped with a note on stderr rather
    than aborting the sweep."""
    # Read only a bounded prefix: the size cap must bind *before* the file is
    # in memory, or one multi-gigabyte note defeats it. Frontmatter always
    # sits at the top, so a prefix is all the parse needs.
    try:
        with path.open("r", encoding="utf-8") as fh:
            text = fh.read(MAX_FRONTMATTER_BYTES + 4096)
    except (OSError, UnicodeDecodeError) as e:
        print(f"warning: {path.name}: unreadable ({e.__class__.__name__}) "
              f"— note skipped", file=sys.stderr)
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3 or len(parts[1].encode("utf-8", "ignore")) > MAX_FRONTMATTER_BYTES:
        # no closing delimiter within the prefix counts as oversized too
        if len(text.encode("utf-8", "ignore")) > MAX_FRONTMATTER_BYTES:
            print(f"warning: {path.name}: frontmatter exceeds "
                  f"{MAX_FRONTMATTER_BYTES} bytes — note skipped", file=sys.stderr)
        return None
    try:
        data = yaml.load(parts[1], Loader=FrontmatterLoader)
    except yaml.YAMLError as e:
        reason = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        print(f"warning: {path.name}: unparseable frontmatter ({reason}) "
              f"— note skipped", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def canonical_keywords(fm: dict, ctx: "Context") -> dict:
    """Rename context-declared keyword aliases to the keywords themselves
    ("type" -> "@type", "id" -> "@id"; SPEC §4.3), so every tool reasons over
    one canonical spelling regardless of which one the author wrote."""
    if not ctx.aliases:
        return fm
    return {ctx.aliases.get(k, k): v for k, v in fm.items()}


def type_values(fm: dict) -> list[str]:
    val = fm.get("@type")
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def is_wikilink(token) -> bool:
    return isinstance(token, str) and token.strip().startswith("[[") and token.strip().endswith("]]")


def wikilink_target(token: str) -> str:
    """The resolvable target of a wiki link (SPEC §4.4.1): alias and fragment
    stripped, any disambiguating path kept.

    [[name|alias]]   -> alias is display-only, ignored
    [[name#Heading]] -> a fragment addresses a location, not a resource
    """
    inner = token.strip()[2:-2]
    inner = inner.split("|", 1)[0]
    inner = inner.split("#", 1)[0]
    return inner.strip()


def wikilink_name(token: str) -> str:
    """Reduce a wiki link to its bare note name (SPEC §4.4.1): the path, when
    present, only selects among same-named notes; the name is the final segment."""
    return wikilink_target(token).rsplit("/", 1)[-1]


def locate(path: Path, vault: Path) -> tuple[str, set[str] | None]:
    """Classify a note by its folder location (SPEC §3).

    Returns (layer, expected_types) where layer is 'schema' or 'data' and
    expected_types is the set of acceptable @type IRIs for that location, or
    None when the location implies no constraint (the instance layer).

    The schema layer is recognised by walking up from the note:
      .../Ontologies/<Name>/Classes/*      -> owl:Class
      .../Ontologies/<Name>/Properties/*   -> owl:{Object,Datatype,...}Property
      .../Ontologies/<Name>/<Name>.md      -> owl:Ontology
      .../Vocabularies/<Scheme>/<Scheme>.md-> skos:ConceptScheme
      .../Vocabularies/<Scheme>/*          -> skos:Concept
    Anything not under Ontologies/ or Vocabularies/ is the data layer.
    """
    parts = path.relative_to(vault).parts
    parent = path.parent.name

    if "Ontologies" in parts:
        if "Classes" in parts:       # possibly nested by hierarchy (SPEC §5.2)
            return "schema", EXPECTED_CLASS
        if "Properties" in parts:
            return "schema", EXPECTED_PROPERTY
        # the ontology resource itself: file sits directly in its named folder
        return "schema", EXPECTED_ONTOLOGY

    if "Vocabularies" in parts:
        # the scheme file shares its name with the folder; the rest are concepts
        if path.stem == parent:
            return "schema", EXPECTED_SCHEME
        return "schema", EXPECTED_CONCEPT

    return "data", None


def governing(path: Path, vault: Path) -> tuple[Path | None, str | None]:
    """Locate the ontology/scheme resource note that owns a schema note.

    Every schema note lives under Ontologies/<Name>/ or Vocabularies/<Name>/,
    and the resource that declares that namespace is the note <Name>/<Name>.md.
    Returns (governing_note_path, name), or (None, None) for the data layer.
    A note can be its own governor (the ontology/scheme note itself).
    """
    parts = path.relative_to(vault).parts
    for anchor in ("Ontologies", "Vocabularies"):
        if anchor in parts:
            i = parts.index(anchor)
            name = parts[i + 1]
            gov = vault.joinpath(*parts[: i + 1], name, name + ".md")
            return gov, name
    return None, None


def vault2rdf_reference(
    vault: Path,
    *,
    context_path: Path | None = None,
    schema_ns: str = "https://example.org/schema/",
    data_ns: str | None = None,
    source: bool = False,
    max_context_bytes: int = MAX_CONTEXT_BYTES,
) -> tuple[str, str, list[str], Graph, Graph, list[tuple[URIRef, URIRef, object, str, str]]]:
    """In-process callable version of vault_to_rdf.

    Returns:
        Turtle documents, warnings, schema/data graphs, and provenance triples.
    """
    context_path = context_path or (vault / "context.jsonld")
    if not context_path.exists():
        raise ConversionError(f"Context not found: {context_path.name}")

    warnings: list[str] = []
    ctx = load_context(context_path, warnings, max_context_bytes)

    root_base = data_ns or ctx.base
    if not root_base:
        root_base = "https://example.org/data/"
        warnings.append(
            "root context declares no @base and no --data-ns given — "
            "instances minted under https://example.org/data/"
        )

    vault_root = vault.resolve()
    discovered: list[tuple[Path, dict, str, set[str] | None]] = []
    for path in sorted(vault.rglob("*.md")):
        if path.is_symlink() or not within_root(path, vault_root):
            warnings.append(
                f"{path.relative_to(vault)} is a symlink or resolves outside the vault — skipped"
            )
            continue
        fm = parse_frontmatter(path)
        if fm is not None:
            fm = canonical_keywords(fm, ctx)
        if fm is None or "@type" not in fm:
            continue
        layer, expected = locate(path, vault)
        discovered.append((path, fm, layer, expected))

    onto_base: dict[Path, str] = {}

    def base_for(gov_path: Path, name: str) -> str:
        onto_dir = gov_path.parent
        if onto_dir not in onto_base:
            base = context_base(onto_dir / "context.jsonld", warnings, max_context_bytes)
            if base is None:
                base = schema_ns.rstrip("/") + "/" + name + "#"
            onto_base[onto_dir] = base
        return onto_base[onto_dir]

    def governing_for(path: Path, layer: str) -> tuple[str, Path]:
        if layer == "schema":
            gov_path, name = governing(path, vault)
            if gov_path is None or name is None:
                raise ConversionError(f"No governing schema context: {path.relative_to(vault)}")
            return base_for(gov_path, name), gov_path.parent
        d = path.parent
        while d != vault and not (d / "context.jsonld").exists():
            d = d.parent
        if d == vault:
            return root_base, vault
        return context_base(d / "context.jsonld", warnings, max_context_bytes) or root_base, d

    def minted_iri(path: Path, layer: str) -> str:
        base, _ = governing_for(path, layer)
        return base + iri_safe(path.stem)

    def subject_iri(path: Path, fm: dict, layer: str) -> URIRef:
        if "@id" in fm:
            token = str(fm["@id"]).strip()
            if not token.startswith(("http://", "https://")):
                warnings.append(
                    f"{path.name}: id '{token}' is not absolute — SPEC §4.5 "
                    f"requires a full http(s) IRI; flattened to base + id"
                )
                base, _ = governing_for(path, layer)
                return URIRef(base + token)
            return URIRef(token)
        return URIRef(minted_iri(path, layer))

    notes: list[tuple[Path, dict, str, URIRef, set[str] | None]] = []
    subject_by_name: dict[str, URIRef] = {}
    subject_by_relpath: dict[str, URIRef] = {}
    first_by_name: dict[str, tuple[Path, URIRef]] = {}
    for path, fm, layer, expected in discovered:
        subj = subject_iri(path, fm, layer)
        notes.append((path, fm, layer, subj, expected))
        rel = path.relative_to(vault).with_suffix("").as_posix()
        subject_by_relpath[rel] = subj
        if path.stem in first_by_name:
            prev_path, prev_subj = first_by_name[path.stem]
            if prev_subj != subj:
                warnings.append(
                    f"ambiguous note name '{path.stem}' "
                    f"({prev_path.relative_to(vault)}, {path.relative_to(vault)}): "
                    f"bare wiki links to it resolve unpredictably — use a "
                    f"path-qualified link or an explicit @id"
                )
            else:
                warnings.append(
                    f"notes {prev_path.relative_to(vault)} and "
                    f"{path.relative_to(vault)} mint the same IRI <{subj}> — "
                    f"they will merge into one subject; give one an "
                    f"explicit absolute id (SPEC §4.5)"
                )
        else:
            first_by_name[path.stem] = (path, subj)
        subject_by_name[path.stem] = subj

        if expected is not None:
            for t in type_values(fm):
                if is_wikilink(t):
                    iri = "[[" + wikilink_name(t) + "]]"
                    warnings.append(
                        f"{path.name}: @type {iri} in a schema folder; "
                        f"expected one of {sorted(expected)}"
                    )
                    continue
                iri = ctx.expand_curie(str(t))
                if iri not in expected:
                    warnings.append(
                        f"{path.name}: @type '{t}' is not an expected "
                        f"type for {path.parent.name}/ "
                        f"(expected one of {sorted(expected)})"
                    )

    def resolve_iri(token: str) -> URIRef:
        if is_wikilink(token):
            target = wikilink_target(token)
            name = target.rsplit("/", 1)[-1]
            if "/" in target:
                hits = {
                    iri
                    for rel, iri in subject_by_relpath.items()
                    if rel == target or rel.endswith("/" + target)
                }
                if len(hits) == 1:
                    return hits.pop()
                if hits:
                    warnings.append(
                        f"wiki link [[{target}]] is ambiguous even with "
                        f"its path — {len(hits)} notes match"
                    )
                    return sorted(hits)[0]
                warnings.append(
                    f"path in [[{target}]] matches no participating note "
                    f"— resolved by note name instead"
                )
            iri = subject_by_name.get(name)
            if iri is None:
                warnings.append(f"dangling wiki link [[{name}]] -> minted under the vault base")
                return URIRef(root_base + iri_safe(name))
            return iri
        return URIRef(ctx.expand_curie(token))

    def canonical_rel(path: Path, expected) -> str:
        if expected is EXPECTED_CLASS:
            return f"Classes/{path.name}"
        if expected is EXPECTED_PROPERTY:
            return f"Properties/{path.name}"
        return path.name

    g_schema, g_data = Graph(), Graph()
    for g in (g_schema, g_data):
        for prefix, ns in ctx.prefixes.items():
            g.bind(prefix, ns)
        g.bind("vld", VLD)
        g.bind("data", root_base)

    provenance_triples: list[tuple[URIRef, URIRef, object, str, str]] = []
    for path, fm, layer, subj, expected in notes:
        g = g_schema if layer == "schema" else g_data
        rel_path = path.relative_to(vault).as_posix()

        if source:
            _, folder = governing_for(path, layer)
            rel_actual = path.relative_to(folder).as_posix()
            if layer == "data":
                divergent = str(subj) != minted_iri(path, layer) or path.parent != folder
            else:
                divergent = rel_actual != canonical_rel(path, expected)
            if divergent:
                g.add((subj, VLD_PATH, Literal(rel_actual)))
                provenance_triples.append((subj, VLD_PATH, Literal(rel_actual), rel_path, layer))

        for key, raw in fm.items():
            if key == "@id":
                continue
            values = raw if isinstance(raw, list) else [raw]

            if key == "@type":
                for v in values:
                    target_iri = resolve_iri(str(v))
                    g.add((subj, RDF.type, target_iri))
                    provenance_triples.append((subj, RDF.type, target_iri, rel_path, layer))
                continue

            term = ctx.terms.get(key)
            if term is None:
                if key in HOST_KEYS:
                    continue
                warnings.append(f"{path.name}: field '{key}' not in context -> skipped")
                continue

            pred = URIRef(ctx.expand_curie(term["@id"]))
            coercion = term.get("@type")

            for v in values:
                rel_path = path.relative_to(vault).as_posix()
                if coercion == "@id":
                    target_iri = resolve_iri(str(v))
                    g.add((subj, pred, target_iri))
                    provenance_triples.append((subj, pred, target_iri, rel_path, layer))
                elif coercion:
                    dt = URIRef(ctx.expand_curie(coercion))
                    lit = Literal(v, datatype=dt)
                    g.add((subj, pred, lit))
                    provenance_triples.append((subj, pred, lit, rel_path, layer))
                else:
                    lit = Literal(v)
                    g.add((subj, pred, lit))
                    provenance_triples.append((subj, pred, lit, rel_path, layer))

    schema_ttl = g_schema.serialize(format="turtle")
    data_ttl = g_data.serialize(format="turtle")
    return (
        schema_ttl,
        data_ttl,
        list(dict.fromkeys(warnings)),
        g_schema,
        g_data,
        provenance_triples,
    )
