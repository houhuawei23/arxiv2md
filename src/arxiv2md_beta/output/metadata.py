"""Save arXiv paper metadata to paper.yml file."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from loguru import logger


def save_paper_metadata(metadata: dict, paper_output_dir: Path) -> None:
    """Save paper metadata to paper.yml file in the output directory.

    Parameters
    ----------
    metadata : dict
        Extended metadata dictionary from fetch_arxiv_metadata
    paper_output_dir : Path
        Output directory where paper.yml will be saved
    """
    if yaml is None:
        logger.warning("PyYAML not installed, skipping paper.yml generation. Install with: pip install pyyaml")
        return

    try:
        paper_yml_data = _metadata_to_paper_yml(metadata)
        if not paper_yml_data:
            logger.debug("No metadata to save, skipping paper.yml")
            return

        output_path = paper_output_dir / "paper.yml"
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(paper_yml_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"Paper metadata saved to: {output_path}")
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")


def merge_paper_yml_preserve_user_fields(existing: dict, fresh: dict) -> dict:
    """Merge API ``fresh`` output into a previously saved ``paper.yml`` (incremental update).

    - **Fresh wins** on keys present in both mappings at the same path (scalar or list).
    - **Keys only in** ``existing`` are preserved (e.g. manual ``urls.website``, ``urls.github``).
    - **Nested dicts** are merged recursively with the same rules.

    Parameters
    ----------
    existing
        Parsed YAML already on disk (may contain user-added fields).
    fresh
        Result of :func:`_metadata_to_paper_yml` from the latest fetch.
    """
    if not isinstance(existing, dict) or not isinstance(fresh, dict):
        return fresh
    return _deep_merge_preserve_user_only_missing(fresh, existing)


def _deep_merge_preserve_user_only_missing(new: dict, old: dict) -> dict:
    """Start from ``new`` (authoritative); add keys from ``old`` only where missing in ``new``."""
    out = dict(new)
    for k, v_old in old.items():
        if k not in out:
            out[k] = v_old
        elif isinstance(v_old, dict) and isinstance(out[k], dict):
            out[k] = _deep_merge_preserve_user_only_missing(out[k], v_old)
    return out


def write_paper_yml_file(
    metadata: dict,
    output_path: Path,
    *,
    merge_existing: dict | None = None,
) -> None:
    """Serialize metadata to a ``paper.yml`` (or ``*.yml``) file at ``output_path``.

    If ``merge_existing`` is set (e.g. from ``paper-yml --update``), user-only keys from the
    existing file are preserved while fresh API fields overwrite.
    """
    if yaml is None:
        logger.warning("PyYAML not installed, cannot write paper.yml. Install with: pip install pyyaml")
        return
    output_path = Path(output_path)
    fresh = _metadata_to_paper_yml(metadata)
    if not fresh:
        logger.warning("No metadata to write (missing arxiv_id); skipping")
        return
    if merge_existing is not None:
        paper_yml_data = merge_paper_yml_preserve_user_fields(merge_existing, fresh)
    else:
        paper_yml_data = fresh
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(paper_yml_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"Paper metadata written to: {output_path}")


def load_paper_yml(path: Path) -> dict:
    """Load a YAML file and return the top-level mapping (e.g. ``{'paper': {...}}``)."""
    if yaml is None:
        raise RuntimeError("PyYAML is required; install with: pip install pyyaml")
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping at root, got {type(data).__name__}")
    return data


def arxiv_id_from_paper_yml_dict(data: dict) -> str:
    """Extract arXiv id from a loaded ``paper.yml`` mapping."""
    paper = data.get("paper")
    if not isinstance(paper, dict):
        raise ValueError("Invalid paper.yml: missing 'paper' object")
    ids = paper.get("identifiers")
    if isinstance(ids, dict) and ids.get("arxiv"):
        return str(ids["arxiv"]).strip()
    pid = paper.get("id")
    if isinstance(pid, str) and pid.startswith("arxiv:"):
        return pid.split(":", 1)[1].strip()
    raise ValueError("Could not find arXiv id in paper.yml (identifiers.arxiv or paper.id)")


def arxiv_id_from_paper_yml(path: Path) -> str:
    """Extract arXiv id string from ``paper.yml`` (``identifiers.arxiv`` or ``paper.id``)."""
    return arxiv_id_from_paper_yml_dict(load_paper_yml(path))


def _metadata_to_paper_yml(metadata: dict) -> dict:
    """Convert arXiv API metadata to nested paper.yml structure.

    与 academic-extension schema 统一，输出嵌套格式。

    Parameters
    ----------
    metadata : dict
        Extended metadata from fetch_arxiv_metadata

    Returns
    -------
    dict
        Dictionary ready for YAML serialization: { paper: { ... } }
    """
    arxiv_id = metadata.get("arxiv_id")
    if not arxiv_id:
        return {}

    title = metadata.get("title") or "Untitled"
    paper_id = f"arxiv:{arxiv_id}"

    # publication
    publication = OrderedDict()
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
    if year is None and metadata.get("year"):
        year = metadata["year"]
    if year is None and date_str:
        try:
            year = int(date_str[:4])
        except (ValueError, TypeError):
            pass
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

    # identifiers
    identifiers = OrderedDict()
    identifiers["arxiv"] = arxiv_id
    if metadata.get("doi"):
        identifiers["doi"] = metadata["doi"]
    if metadata.get("openalex_work_id"):
        identifiers["openalex_work"] = metadata["openalex_work_id"]

    # urls
    urls = OrderedDict()
    if metadata.get("pdf_url"):
        urls["pdf"] = metadata["pdf_url"]
    if metadata.get("abstract_url"):
        urls["abstract"] = metadata["abstract_url"]

    # authors
    authors = []
    for author in metadata.get("authors", []):
        author_dict = {}
        if author.get("name"):
            author_dict["name"] = author["name"]
        affs = author.get("affiliations")
        if isinstance(affs, list) and affs:
            author_dict["affiliations"] = [str(x) for x in affs if x]
        elif author.get("affiliation"):
            # Only emit string when no structured list (avoids duplicating join(affiliations))
            author_dict["affiliation"] = author["affiliation"]
        if author.get("orcid"):
            author_dict["orcid"] = author["orcid"]
        if author_dict:
            authors.append(author_dict)

    # content
    content = OrderedDict()
    if metadata.get("summary"):
        content["abstract"] = metadata["summary"]
    keywords = []
    categories = metadata.get("categories", [])
    if categories:
        keywords.extend(categories)
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
    content["language"] = "en"
    if metadata.get("citation"):
        content["citation"] = metadata["citation"]

    # workflow
    workflow = OrderedDict()
    workflow["status"] = "unread"
    workflow["priority"] = "normal"
    workflow["date_added"] = date_str or datetime.now().strftime("%Y-%m-%d")

    # relations
    relations = OrderedDict()
    relations["tags"] = []
    relations["categories"] = list(categories) if categories else []
    if metadata.get("primary_category"):
        relations["primary_category"] = metadata["primary_category"]
    relations["related"] = []

    # Build paper object with fixed field order
    paper = OrderedDict()
    paper["id"] = paper_id
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
    paper["workflow"] = dict(workflow)
    paper["relations"] = dict(relations)
    if metadata.get("bibtex"):
        paper["bibtex"] = metadata["bibtex"]

    return {"paper": dict(paper)}
