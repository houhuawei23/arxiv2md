"""Save arXiv paper metadata to paper.yml file."""

from __future__ import annotations

import re
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
        if author.get("affiliation"):
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
