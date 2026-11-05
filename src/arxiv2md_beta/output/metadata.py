"""Save arXiv paper metadata to paper.yml file."""

from __future__ import annotations

import re
import shutil
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from arxiv2md_beta.exceptions import ParseError
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.atomic_io import atomic_write_text_sync

# Paths (relative to the serialized root) the user owns once present in a
# saved paper.yml: ``paper-yml --update`` and re-conversions must never let
# fresh API data clobber them (audit4 A4 — reading status / tags used to
# reset on every update). Fresh values fill these keys only when absent.
# (v1's ``paper.bibtex`` moved to a ``paper.bib`` sidecar in schema v2; the
# sidecar keeps the user-owned rule via the extracted-bibtex preference in
# :func:`write_paper_yml_file`.)
USER_OWNED_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("paper", "workflow", "status"),
        ("paper", "workflow", "priority"),
        ("paper", "workflow", "date_added"),
        ("paper", "relations", "tags"),
        ("paper", "relations", "related"),
    }
)

# Schema version marker written under ``paper.schema``; files without it (or
# with a lower value) are upgraded by :func:`migrate_paper_yml_v1_to_v2`
# before they are merged or re-serialized.
PAPER_YML_SCHEMA_VERSION = 2


def save_paper_metadata(metadata: dict, paper_output_dir: Path) -> None:
    """Save paper metadata to paper.yml file in the output directory.

    Thin wrapper over :func:`write_paper_yml_file` so the conversion pipeline
    and the ``paper-yml`` command share one serialization path. Differences:
    empty metadata is skipped silently (local sources may have none) and all
    errors are swallowed with a warning (paper.yml is best-effort).

    Parameters
    ----------
    metadata : dict
        Extended metadata dictionary from fetch_arxiv_metadata
    paper_output_dir : Path
        Output directory where paper.yml will be saved
    """
    try:
        if not _metadata_to_paper_yml(metadata):
            logger.debug("No metadata to save, skipping paper.yml")
            return
        target = paper_output_dir / "paper.yml"
        # Re-running convert in an existing directory used to reset every
        # hand-edited field (audit5 C6): the USER_OWNED_PATHS protection only
        # covered ``paper-yml --update``. Read the previous file and merge so
        # user-owned keys survive; an unreadable previous file degrades to
        # the plain overwrite (which still leaves a .bak) below.
        merge_existing: dict | None = None
        if target.exists():
            try:
                merge_existing = load_paper_yml(target)
            except Exception as e:  # noqa: BLE001 - best-effort merge
                logger.warning(f"Existing paper.yml unreadable, overwriting: {e}")
        write_paper_yml_file(metadata, target, merge_existing=merge_existing)
    except Exception as e:  # noqa: BLE001 - paper.yml is best-effort by design
        logger.warning(f"Failed to save paper.yml: {e}")


def merge_paper_yml_preserve_user_fields(existing: dict, fresh: dict, *, refresh: bool = False) -> dict:
    """Merge API ``fresh`` output into a previously saved ``paper.yml`` (incremental update).

    - **Fresh wins** on keys present in both mappings at the same path (scalar or list).
    - **Keys only in** ``existing`` are preserved (e.g. manual ``urls.website``, ``urls.github``).
    - **User-owned paths** (:data:`USER_OWNED_PATHS`) invert the rule: the value
      already on disk wins — fresh data only fills genuine gaps.
    - **Nested dicts** are merged recursively with the same rules.
    - ``refresh=True`` (``paper-yml --update --refresh``) flips the user-owned
      rule back: a fresh API value replaces the on-disk one wherever the API
      provides one. Keys only the user added are still kept — a refresh
      resets fields, it does not destroy them.

    Parameters
    ----------
    existing
        Parsed YAML already on disk (may contain user-added fields).
    fresh
        Result of :func:`_metadata_to_paper_yml` from the latest fetch.
    refresh
        Let API values overwrite user-owned fields too (audit5 S8 F3).
    """
    if not isinstance(existing, dict) or not isinstance(fresh, dict):
        return fresh
    return _deep_merge_preserve_user_only_missing(fresh, existing, refresh=refresh)


def _deep_merge_preserve_user_only_missing(
    new: dict, old: dict, _path: tuple[str, ...] = (), *, refresh: bool = False
) -> dict:
    """Start from ``new`` (authoritative); add keys from ``old`` only where missing in ``new``.

    Paths listed in :data:`USER_OWNED_PATHS` take ``old`` unconditionally: the
    user edited them on disk and fresh API data must not clobber that
    (audit4 A4 — reading status / tags used to reset on every update).
    With ``refresh=True`` they take ``new`` instead — but only where ``new``
    actually has a value; a key the API dropped still falls back to ``old``
    rather than disappearing.
    """
    out = dict(new)
    for k, v_old in old.items():
        path = _path + (k,)
        if path in USER_OWNED_PATHS:
            if refresh and k in out:
                continue
            out[k] = v_old
            continue
        if k not in out:
            out[k] = v_old
        elif isinstance(v_old, dict) and isinstance(out[k], dict):
            out[k] = _deep_merge_preserve_user_only_missing(out[k], v_old, path, refresh=refresh)
    return out


def write_paper_yml_file(
    metadata: dict,
    output_path: Path,
    *,
    merge_existing: dict | None = None,
    refresh: bool = False,
) -> None:
    """Serialize metadata to a ``paper.yml`` (or ``*.yml``) file at ``output_path``.

    If ``merge_existing`` is set (e.g. from ``paper-yml --update``), user-only keys from the
    existing file are preserved while fresh API fields overwrite; a v1 file is
    migrated to schema v2 before merging. ``refresh`` additionally lets API
    values replace user-owned fields (audit5 S8 F3).

    The BibTeX entry lives in a sibling ``paper.bib`` (schema v2), never
    embedded in the YAML: an existing entry wins unless ``refresh`` (same
    user-owned rule ``paper.bibtex`` had in v1), otherwise the freshly
    generated one is written.
    """
    output_path = Path(output_path)
    fresh = _metadata_to_paper_yml(metadata)
    if not fresh:
        logger.warning("No metadata to write (missing arxiv_id); skipping")
        return
    existing_bibtex: str | None = None
    if merge_existing is not None:
        merge_existing, existing_bibtex = migrate_paper_yml_v1_to_v2(merge_existing)
        paper_yml_data = merge_paper_yml_preserve_user_fields(merge_existing, fresh, refresh=refresh)
    else:
        paper_yml_data = fresh
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write + two-generation backup: the file may carry hand-edited
    # user fields, so a crash mid-write must not destroy it (audit4 S5.2).
    # One generation was not enough — two consecutive bad writes lost the
    # only good copy, so the previous .bak rotates to .bak2 first (audit5
    # R-11).
    if output_path.exists():
        backup = output_path.with_suffix(output_path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, output_path.with_suffix(output_path.suffix + ".bak2"))
        shutil.copy2(output_path, backup)
    content = yaml.dump(paper_yml_data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    atomic_write_text_sync(output_path, content)
    logger.info(f"Paper metadata written to: {output_path}")
    _write_bib_sidecar(metadata, output_path, existing_bibtex=existing_bibtex, refresh=refresh)


def _write_bib_sidecar(metadata: dict, output_path: Path, *, existing_bibtex: str | None, refresh: bool) -> None:
    """Write the BibTeX entry to the sibling ``paper.bib`` (schema v2 home).

    User-owned rule carried over from v1's ``paper.bibtex``: an entry already
    on disk wins over the freshly generated one unless ``refresh``. A missing
    fresh entry never deletes an existing sidecar.
    """
    fresh_bibtex = str(metadata.get("bibtex") or "").strip()
    sidecar = existing_bibtex if (existing_bibtex and not refresh) else None
    bib = sidecar or fresh_bibtex or existing_bibtex
    if not bib:
        return
    bib_path = bib_sidecar_path(output_path)
    if bib_path.exists() and bib == bib_path.read_text(encoding="utf-8").strip():
        return
    atomic_write_text_sync(bib_path, bib.strip() + "\n")
    logger.info(f"BibTeX written to: {bib_path}")


def migrate_paper_yml_v1_to_v2(data: dict) -> tuple[dict, str | None]:
    """Upgrade a loaded ``paper.yml`` mapping from schema v1 to v2, in place.

    Returns ``(data, bibtex)`` where *bibtex* is the entry extracted from the
    v1 ``paper.bibtex`` key (now a ``paper.bib`` sidecar — the caller writes
    it), or ``None`` when there was none. Idempotent: a file already at v2
    (``paper.schema == 2``) or with no v1 markers passes through unchanged.

    Moves performed:
    - ``relations.categories`` / ``relations.primary_category`` → ``classification.*``
    - arXiv-category entries removed from ``content.keywords`` (v1 mixed
      classification into keywords)
    - ``paper.id`` (``arxiv:<id>``) dropped — ``identifiers.arxiv`` is the identity
    - ``content.citation`` (truncated derived string) dropped
    - scalar ``authors[].affiliation`` → ``affiliations: [v]``
    - ``paper.bibtex`` extracted and the key removed
    - ``paper.schema: 2`` written
    """
    paper = data.get("paper") if isinstance(data, dict) else None
    if not isinstance(paper, dict):
        return data, None
    if paper.get("schema") == PAPER_YML_SCHEMA_VERSION:
        return data, None

    relations = paper.get("relations")
    relations = relations if isinstance(relations, dict) else {}
    content = paper.get("content")
    content = content if isinstance(content, dict) else {}
    has_v1_markers = any(
        (
            "id" in paper,
            "bibtex" in paper,
            "categories" in relations,
            "primary_category" in relations,
            "citation" in content,
        )
    )
    if not has_v1_markers:
        # Already structurally v2 (e.g. produced by a v2-aware writer); just
        # stamp the version if missing.
        paper["schema"] = PAPER_YML_SCHEMA_VERSION
        return data, None

    # classification ← relations
    classification = paper.get("classification")
    classification = classification if isinstance(classification, dict) else {}
    if not classification.get("categories") and relations.get("categories"):
        classification["categories"] = relations["categories"]
    if not classification.get("primary_category") and relations.get("primary_category"):
        classification["primary_category"] = relations["primary_category"]
    relations.pop("categories", None)
    relations.pop("primary_category", None)
    if classification:
        paper["classification"] = classification

    # keywords: drop entries that are (case-insensitively) categories; a
    # list left empty by that removal is removed outright (v1 papers whose
    # only keywords were categories end up with no keywords at all).
    cats = {str(c).lower() for c in (classification.get("categories") or [])}
    if cats and isinstance(content.get("keywords"), list):
        content["keywords"] = [kw for kw in content["keywords"] if str(kw).lower() not in cats]
        if not content["keywords"]:
            content.pop("keywords")
    content.pop("citation", None)

    # identity + citation-string cleanup
    paper.pop("id", None)

    # authors: scalar affiliation → list
    for au in paper.get("authors") or []:
        if isinstance(au, dict) and not isinstance(au.get("affiliations"), list) and au.get("affiliation"):
            au["affiliations"] = [au.pop("affiliation")]

    # bibtex extracted (caller writes the sidecar)
    bibtex = paper.pop("bibtex", None)
    bibtex = str(bibtex).strip() if bibtex else None

    paper["schema"] = PAPER_YML_SCHEMA_VERSION
    return data, bibtex or None


def bib_sidecar_path(output_path: Path) -> Path:
    """Sibling ``paper.bib`` path for a ``paper.yml`` at *output_path*."""
    return output_path.with_suffix(".bib")


def load_paper_yml(path: Path) -> dict:
    """Load a YAML file and return the top-level mapping (e.g. ``{'paper': {...}}``)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ParseError(f"Expected YAML mapping at root, got {type(data).__name__}")
    return data


def arxiv_id_from_paper_yml_dict(data: dict) -> str:
    """Extract arXiv id from a loaded ``paper.yml`` mapping."""
    paper = data.get("paper")
    if not isinstance(paper, dict):
        raise ParseError("Invalid paper.yml: missing 'paper' object")
    ids = paper.get("identifiers")
    if isinstance(ids, dict) and ids.get("arxiv"):
        return str(ids["arxiv"]).strip()
    pid = paper.get("id")
    if isinstance(pid, str) and pid.startswith("arxiv:"):
        return pid.split(":", 1)[1].strip()
    raise ParseError("Could not find arXiv id in paper.yml (identifiers.arxiv or paper.id)")


def arxiv_id_from_paper_yml(path: Path) -> str:
    """Extract arXiv id string from ``paper.yml`` (``identifiers.arxiv`` or ``paper.id``)."""
    return arxiv_id_from_paper_yml_dict(load_paper_yml(path))


def _metadata_to_paper_yml(metadata: dict) -> dict:
    """Convert arXiv API metadata to the nested paper.yml structure (schema v2).

    v2 layout (see ``PAPER_YML_SCHEMA_VERSION``):

    - ``paper.schema`` marks the version (migration detection).
    - Categories live only in ``paper.classification`` — arXiv categories are
      classification, not keywords, and v1 duplicated them in
      ``content.keywords`` + ``relations.categories``.
    - ``paper.id`` (``arxiv:<id>``) and the truncated ``content.citation`` are
      gone; ``identifiers.arxiv`` / ``identifiers.doi`` carry the identity.
    - ``bibtex`` is no longer embedded (escaped multi-line YAML) — it is
      written to a sibling ``paper.bib`` by the write layer.
    - ``publication.updated_date`` / ``identifiers.doi`` / ``content.license``
      are emitted when the upstream metadata provides them (DOI additionally
      derives from the arXiv DataCite convention when absent).

    Parameters
    ----------
    metadata : dict
        Extended metadata from fetch_arxiv_metadata

    Returns:
    -------
    dict
        Dictionary ready for YAML serialization: { paper: { ... } }
    """
    arxiv_id = metadata.get("arxiv_id")
    if not arxiv_id:
        return {}

    title = metadata.get("title") or "Untitled"

    # publication
    publication: OrderedDict[str, Any] = OrderedDict()
    publication["type"] = "preprint"
    publication["venue"] = "arXiv"

    date_str = None
    year = None
    if metadata.get("published_print_date"):
        date_str = metadata["published_print_date"]
        year = metadata.get("published_print_year")
    elif metadata.get("published_online_date"):
        date_str = metadata["published_online_date"]
        year = metadata.get("published_online_year")
    elif metadata.get("date"):
        date_str = metadata["date"]
    elif metadata.get("published"):
        try:
            dt = datetime.fromisoformat(metadata["published"].replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
            year = metadata.get("year") or int(dt.strftime("%Y"))
        except Exception:
            pass

    if date_str:
        publication["date_published"] = date_str
    updated = _date_only(metadata.get("updated"))
    if updated and updated != date_str:
        publication["updated_date"] = updated
    if year is None and metadata.get("year"):
        year = metadata["year"]
    if year is None and date_str:
        with suppress(ValueError, TypeError):
            year = int(date_str[:4])
    if year is not None:
        publication["year"] = int(year) if isinstance(year, str) and str(year).isdigit() else year

    # Crossref type mapping
    if metadata.get("crossref_type"):
        crossref_type = metadata["crossref_type"].lower()
        type_mapping = {
            "journal-article": "journal",
            "proceedings-article": "conference",
            "book-chapter": "book",
            "dissertation": "thesis",
            "report": "techreport",
        }
        publication["type"] = type_mapping.get(crossref_type, "preprint")

    # identifiers — DOI derives from the arXiv DataCite convention when the
    # upstream sources did not provide one (only for real arXiv ids; local
    # archives may carry synthetic ids that have no DOI).
    identifiers = OrderedDict()
    identifiers["arxiv"] = arxiv_id
    doi = str(metadata.get("doi") or "").strip()
    if not doi and re.match(r"\d{4}\.\d{4,5}", strip_version(str(arxiv_id))):
        doi = f"10.48550/arXiv.{strip_version(str(arxiv_id))}"
    if doi:
        identifiers["doi"] = doi
    if metadata.get("openalex_work_id"):
        identifiers["openalex_work"] = metadata["openalex_work_id"]

    # urls
    urls = OrderedDict()
    if metadata.get("pdf_url"):
        urls["pdf"] = metadata["pdf_url"]
    if metadata.get("abstract_url"):
        urls["abstract"] = metadata["abstract_url"]

    # authors — affiliations are list-only in v2 (the legacy scalar
    # ``affiliation`` join duplicated the list).
    authors = []
    for author in metadata.get("authors", []):
        author_dict = {}
        if author.get("name"):
            author_dict["name"] = author["name"]
        affs = author.get("affiliations")
        if not isinstance(affs, list) and author.get("affiliation"):
            affs = [author["affiliation"]]
        if isinstance(affs, list) and affs:
            author_dict["affiliations"] = [str(x) for x in affs if x]
        if author.get("orcid"):
            author_dict["orcid"] = author["orcid"]
        if author_dict:
            authors.append(author_dict)

    categories = metadata.get("categories", [])

    # content — real keywords only; arXiv categories belong to classification.
    content = OrderedDict()
    if metadata.get("summary"):
        content["abstract"] = metadata["summary"]
    keywords = []
    if metadata.get("keywords_merged"):
        keywords = metadata["keywords_merged"]
    elif metadata.get("crossref_subjects"):
        keywords.extend(kw for kw in metadata["crossref_subjects"] if kw not in keywords)
    if keywords:
        seen = set()
        unique_keywords = []
        for kw in keywords:
            kw_lower = str(kw).lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique_keywords.append(kw)
        content["keywords"] = unique_keywords
    # Prefer an explicit language from the source; "en" remains the arXiv default.
    content["language"] = metadata.get("language") or "en"
    if metadata.get("license"):
        content["license"] = metadata["license"]

    # classification — the single home for arXiv categories (v1 scattered
    # them across content.keywords and relations.categories).
    classification = OrderedDict()
    if categories:
        classification["categories"] = list(categories)
    if metadata.get("primary_category"):
        classification["primary_category"] = metadata["primary_category"]

    # workflow — date_added records when the paper entered the library, not
    # its arXiv publication date (audit4 A4); the merge layer keeps any
    # user-owned value already on disk.
    workflow = OrderedDict()
    workflow["status"] = "unread"
    workflow["priority"] = "normal"
    # UTC, not local: around midnight the same paper used to get different
    # entry dates depending on the machine's timezone (audit5 T-11).
    workflow["date_added"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # relations
    relations: OrderedDict[str, Any] = OrderedDict()
    relations["tags"] = []
    relations["related"] = []

    # Build paper object with fixed field order
    paper: OrderedDict[str, Any] = OrderedDict()
    paper["schema"] = PAPER_YML_SCHEMA_VERSION
    paper["title"] = title
    if authors:
        paper["authors"] = authors
    if publication:
        paper["publication"] = dict(publication)
    if identifiers:
        paper["identifiers"] = dict(identifiers)
    if urls:
        paper["urls"] = dict(urls)
    if content:
        paper["content"] = dict(content)
    if classification:
        paper["classification"] = dict(classification)
    paper["workflow"] = dict(workflow)
    paper["relations"] = dict(relations)

    return {"paper": dict(paper)}


def _date_only(value: Any) -> str | None:
    """Normalize an upstream datetime/date string to ``YYYY-MM-DD`` (None if unparsable)."""
    if not value:
        return None
    text = str(value).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    with suppress(ValueError):
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    return None
