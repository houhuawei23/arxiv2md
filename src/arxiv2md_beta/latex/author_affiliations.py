"""Parse author affiliations from LaTeX source and merge into API metadata."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from arxiv2md_beta.latex import tex_source as tex_source_mod
from arxiv2md_beta.latex.tex_source import TexSourceInfo
from arxiv2md_beta.network.author_enrichment import _dedupe_affiliation_strings, _names_match


def parse_author_affiliations_from_tex(tex: str) -> list[dict[str, Any]]:
    """Extract ``[{name, affiliations}, ...]`` from expanded TeX (best-effort).

    Handles common patterns: ICML ``\\icmlauthor`` / ``\\icmlaffiliation`` (icml2026),
    IEEE ``\\IEEEauthorblockN`` / ``\\IEEEauthorblockA``,
    ICLR/NeurIPS-style ``\\author`` with ``$^{n}$`` markers and a legend line,
    sequential ``\\author`` + ``\\affiliation`` / ``\\institution`` / ``\\institute``,
    and single ``\\author{...}`` blocks split by ``\\and``.
    """
    if not tex or not tex.strip():
        return []
    region = _extract_author_region(tex)
    region = _strip_tex_comments(region)
    if not region.strip():
        return []

    icml = _parse_icml(region)
    if icml:
        return icml

    ieee = _parse_ieee(region)
    if ieee:
        return ieee

    iclr_inline = _parse_iclr_neurips_superscript_author(region)
    if iclr_inline:
        return iclr_inline

    seq = _parse_sequential_author_affiliation(region)
    if seq:
        return seq

    return _parse_single_author_block(region)


def merge_tex_affiliations_into_metadata(
    metadata: dict[str, Any],
    tex_source_info: TexSourceInfo | None,
) -> int:
    """Merge TeX-parsed affiliations into ``metadata['authors']``. Returns match count.

    Merges by name with Atom/API order; dedupes affiliation strings. Safe no-op if
    TeX missing or parsing yields nothing.
    """
    if not tex_source_info or not tex_source_info.main_tex_file:
        return 0
    expanded = tex_source_mod.expand_tex_source_for_parsing(tex_source_info)
    parsed = parse_author_affiliations_from_tex(expanded)
    if not parsed:
        logger.debug(
            "TeX affiliation parse: no ICML/IEEE/\\author blocks with affiliations found "
            f"(main_tex={tex_source_info.main_tex_file})"
        )
        return 0
    authors = metadata.get("authors")
    if not isinstance(authors, list) or not authors:
        return 0

    matched = 0
    for au in authors:
        name = (au.get("name") or "").strip()
        if not name:
            continue
        for ta in parsed:
            tname = (ta.get("name") or "").strip()
            taffs = ta.get("affiliations") or []
            if not isinstance(taffs, list):
                taffs = [str(taffs)] if taffs else []
            if not tname or not taffs:
                continue
            if not _names_match(name, tname):
                continue
            existing: list[str] = []
            if isinstance(au.get("affiliations"), list):
                existing = [str(x) for x in au["affiliations"] if x]
            elif au.get("affiliation"):
                existing = [str(au["affiliation"]).strip()]
            merged = _dedupe_affiliation_strings(existing + [str(x).strip() for x in taffs if x])
            if merged:
                au["affiliations"] = merged
                au.pop("affiliation", None)
                matched += 1
            break

    if matched:
        logger.info(f"Merged TeX affiliations for {matched} author(s)")
    elif parsed and any((ta.get("affiliations") or []) for ta in parsed):
        logger.warning(
            f"TeX affiliation parse found {len(parsed)} author(s) from source but "
            f"matched 0 to API author names — check spelling/order"
        )
    return matched


# --- region extraction ---


def _extract_author_region(tex: str) -> str:
    """Slice likely author markup: body before abstract / first section, plus preamble if needed.

    Many conference styles (ICLR, NeurIPS) place ``\\author{...}`` in the **preamble**
    before ``\\begin{document}``. Without including the preamble, those blocks are invisible.
    """
    m = re.search(r"\\begin\s*\{\s*document\s*\}", tex, re.I)
    preamble = tex[: m.start()] if m else ""
    start = m.end() if m else 0
    end = len(tex)
    tail = tex[start:]
    for pat in (
        r"\\begin\s*\{\s*abstract\s*\}",
        r"\\maketitle",
        r"\\section\s*\*?\s*\{",
        r"\\chapter\s*\*?\s*\{",
    ):
        mm = re.search(pat, tail, re.I)
        if mm:
            end = min(end, start + mm.start())
    body = tex[start:end]
    if "\\author" in preamble:
        return (preamble + "\n" + body)[:300000]
    return body[:250000]


def _strip_tex_comments(text: str) -> str:
    out_lines: list[str] = []
    for line in text.splitlines():
        if "%" not in line:
            out_lines.append(line)
            continue
        buf: list[str] = []
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                break
            buf.append(line[i])
            i += 1
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


# --- balanced braces ---


def _balanced_inner(s: str, open_brace_idx: int) -> str | None:
    if open_brace_idx >= len(s) or s[open_brace_idx] != "{":
        return None
    depth = 0
    i = open_brace_idx
    while i < len(s):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[open_brace_idx + 1 : i]
        i += 1
    return None


# --- ICML (icml2026): \icmlauthor{Name}{key,key} + \icmlaffiliation{key}{Institution} ---


def _parse_icml_affiliation_map(text: str) -> dict[str, str]:
    """Map short affiliation keys to institution strings."""
    out: dict[str, str] = {}
    for m in re.finditer(r"\\icmlaffiliation\s*\{", text, re.I):
        o1 = m.end() - 1
        key = _balanced_inner(text, o1)
        if key is None:
            continue
        depth = 0
        k = o1
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        while k < len(text) and text[k] in " \t\n":
            k += 1
        if k >= len(text) or text[k] != "{":
            continue
        inst = _balanced_inner(text, k)
        if inst is None:
            continue
        out[key.strip()] = _clean_affil_line(inst)
    return out


def _parse_icml(region: str) -> list[dict[str, Any]]:
    if "\\icmlauthor" not in region:
        return []
    aff_map = _parse_icml_affiliation_map(region)
    if not aff_map:
        return []
    authors: list[dict[str, Any]] = []
    for m in re.finditer(r"\\icmlauthor\s*\{", region, re.I):
        o1 = m.end() - 1
        name_raw = _balanced_inner(region, o1)
        if name_raw is None:
            continue
        depth = 0
        k = o1
        end_name = len(region)
        while k < len(region):
            if region[k] == "{":
                depth += 1
            elif region[k] == "}":
                depth -= 1
                if depth == 0:
                    end_name = k + 1
                    break
            k += 1
        k = end_name
        while k < len(region) and region[k] in " \t\n":
            k += 1
        if k >= len(region) or region[k] != "{":
            continue
        keys_raw = _balanced_inner(region, k)
        if keys_raw is None:
            continue
        keys = [p.strip() for p in keys_raw.split(",") if p.strip()]
        affils: list[str] = []
        for key in keys:
            if key in aff_map:
                affils.append(aff_map[key])
        affils = _dedupe_affiliation_strings(affils)
        name = _clean_latex_author_name(name_raw)
        if name:
            authors.append({"name": name, "affiliations": affils})
    return authors


# --- ICLR / NeurIPS: \author{ Name$^{1}$ \\ $^{1}$ Org, $^{2}$ Org2 \\ ... } in preamble or body ---


def _parse_superscript_legend(legend: str) -> dict[str, str]:
    """Map numeric markers like ``1`` -> ``Stanford University`` from a legend line."""
    out: dict[str, str] = {}
    legend = legend.strip()
    for seg in legend.split(","):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r"\$\^\{([^}]*)\}\$\s*(.+)$", seg)
        if not m:
            continue
        key, rest = m.group(1).strip(), m.group(2).strip()
        if key.isdigit():
            out[key] = _clean_affil_line(rest)
    return out


def _affiliation_markers_from_superscript(sup_body: str, aff_map: dict[str, str]) -> list[str]:
    """Turn ``*12`` or ``1`` into digit keys present in ``aff_map`` (e.g. 12 -> 1 and 2)."""
    raw = sup_body.replace("*", "").strip()
    if not raw:
        return []
    if raw in aff_map:
        return [raw]
    out: list[str] = []
    for ch in raw:
        if ch.isdigit() and ch in aff_map and ch not in out:
            out.append(ch)
    return out


def _parse_iclr_neurips_superscript_author(region: str) -> list[dict[str, Any]]:
    """Parse ``\\author{... Name$^{n}$ ... \\\\ $^{n}$ Institution ...}`` (ICLR/NeurIPS camera-ready)."""
    m = re.search(r"\\author\s*\{", region, re.I)
    if not m:
        return []
    o = m.end() - 1
    inner = _balanced_inner(region, o)
    if inner is None or "\\\\" not in inner:
        return []
    lines = [ln.strip() for ln in re.split(r"\\\\", inner) if ln.strip()]
    if len(lines) < 2:
        return []
    legend = lines[1]
    if not re.search(r"\$\^\{[^}]*\}\$", legend):
        return []
    aff_map = _parse_superscript_legend(legend)
    if not aff_map:
        return []
    author_line = lines[0]
    results: list[dict[str, Any]] = []
    for segment in _split_author_segments_iclr(author_line):
        name, sups = _parse_one_author_segment_superscripts(segment)
        if not name:
            continue
        affils: list[str] = []
        for sup in sups:
            for mk in _affiliation_markers_from_superscript(sup, aff_map):
                if mk in aff_map:
                    affils.append(aff_map[mk])
        affils = _dedupe_affiliation_strings(affils)
        cn = _clean_latex_author_name(name)
        if cn:
            results.append({"name": cn, "affiliations": affils})
    return results


def _split_author_segments_iclr(author_line: str) -> list[str]:
    """Comma-separated author segments (top-level commas)."""
    return [p.strip() for p in author_line.split(",") if p.strip()]


def _parse_one_author_segment_superscripts(segment: str) -> tuple[str, list[str]]:
    """Return (name_without_marks, list of superscript bodies inside ``$^{...}$``)."""
    sups = re.findall(r"\$\^\{([^}]*)\}\$", segment)
    name = re.sub(r"\$\^\{[^}]*\}\$", "", segment)
    name = re.sub(r"\s+", " ", name).strip()
    return name, sups


# --- IEEE ---


def _parse_ieee(header: str) -> list[dict[str, Any]]:
    if r"\IEEEauthorblockN" not in header:
        return []
    results: list[dict[str, Any]] = []
    for m in re.finditer(r"\\IEEEauthorblockN\s*\{", header, re.I):
        o = m.end() - 1
        name_raw = _balanced_inner(header, o)
        if name_raw is None:
            continue
        name = _clean_latex_author_name(name_raw)
        j = o
        depth = 0
        after_n = len(header)
        while j < len(header):
            if header[j] == "{":
                depth += 1
            elif header[j] == "}":
                depth -= 1
                if depth == 0:
                    after_n = j + 1
                    break
            j += 1
        rest = header[after_n:]
        m2 = re.search(r"\\IEEEauthorblockA\s*\{", rest, re.I)
        affils: list[str] = []
        if m2:
            o2 = after_n + m2.end() - 1
            a_inner = _balanced_inner(header, o2)
            if a_inner:
                affils = _split_address_lines(a_inner)
        if name:
            results.append({"name": name, "affiliations": affils})
    return results


# --- sequential \\author + affiliation ---


_AUTHOR_CMD = re.compile(
    r"\\(?:author|Author)\s*(?:\[[^\]]*\])?\s*\{",
    re.I,
)


def _parse_sequential_author_affiliation(header: str) -> list[dict[str, Any]]:
    matches = list(_AUTHOR_CMD.finditer(header))
    if not matches:
        return []

    results: list[dict[str, Any]] = []
    for idx, m in enumerate(matches):
        brace_open = m.end() - 1
        inner = _balanced_inner(header, brace_open)
        if inner is None:
            continue
        j = brace_open
        depth = 0
        end_author = len(header)
        while j < len(header):
            if header[j] == "{":
                depth += 1
            elif header[j] == "}":
                depth -= 1
                if depth == 0:
                    end_author = j + 1
                    break
            j += 1
        next_author = matches[idx + 1].start() if idx + 1 < len(matches) else len(header)
        tail = header[end_author:next_author]
        names = _split_author_names(inner)
        affils_tail = _collect_affiliation_commands(tail)
        if not names:
            continue
        if affils_tail:
            if len(names) == 1:
                cn = _clean_latex_author_name(names[0])
                if cn:
                    results.append({"name": cn, "affiliations": affils_tail})
            else:
                for n in names:
                    cn = _clean_latex_author_name(n)
                    if cn:
                        results.append({"name": cn, "affiliations": list(affils_tail)})
        else:
            for n in names:
                cn = _clean_latex_author_name(n)
                if cn:
                    results.append({"name": cn, "affiliations": []})

    if results and any(r.get("affiliations") for r in results):
        return results
    return []


def _collect_affiliation_commands(tail: str) -> list[str]:
    out: list[str] = []
    for cmd in (
        r"\\affiliation\s*\{",
        r"\\institution\s*\{",
        r"\\institute\s*\{",
        r"\\address\s*\{",
    ):
        for m in re.finditer(cmd, tail, re.I):
            o = m.end() - 1
            inner = _balanced_inner(tail, o)
            if inner:
                flat = _flatten_affil_inner(inner)
                if flat:
                    out.append(flat)
    return _dedupe_affiliation_strings(out)


def _flatten_affil_inner(inner: str) -> str:
    """Pick readable institution line from nested acmart-style blocks."""
    inst = re.search(r"\\institution\s*\{", inner, re.I)
    if inst:
        o = inst.end() - 1
        sub = _balanced_inner(inner, o)
        if sub:
            return _clean_affil_line(sub)
    # strip nested envs lightly
    t = inner.replace("\n", " ")
    t = re.sub(r"\\[a-zA-Z@]+\s*\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _clean_affil_line(t) if t else ""


def _strip_thanks_blocks(s: str) -> str:
    """Remove ``\\thanks{...}`` using balanced braces (handles ``$^{\\ast}$`` inside)."""
    while True:
        m = re.search(r"\\thanks\s*\{", s, re.I)
        if not m:
            return s.strip()
        brace_open = m.end() - 1
        if brace_open >= len(s) or s[brace_open] != "{":
            return s.strip()
        depth = 0
        end = len(s)
        i = brace_open
        while i < len(s):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1
        s = s[: m.start()] + " " + s[end:]


def _split_author_names(inner: str) -> list[str]:
    inner = _strip_thanks_blocks(inner)
    if re.search(r"\\(?:and|AND)\b", inner):
        parts = re.split(r"\\(?:and|AND)\b", inner)
        return [p.strip() for p in parts if p.strip()]
    # IEEEtran / many venues: one line "Name1, Name2, Name3" (no \and)
    return [p.strip() for p in inner.split(",") if p.strip()]


def _parse_single_author_block(header: str) -> list[dict[str, Any]]:
    m = re.search(_AUTHOR_CMD, header)
    if not m:
        return []
    o = m.end() - 1
    inner = _balanced_inner(header, o)
    if not inner:
        return []
    names = _split_author_names(inner)
    affils = _collect_affiliation_commands(header[m.end() : m.end() + 8000])
    if not names:
        return []
    if len(names) == 1:
        n = _clean_latex_author_name(names[0])
        return [{"name": n, "affiliations": affils}] if n else []
    return [{"name": _clean_latex_author_name(n), "affiliations": []} for n in names if _clean_latex_author_name(n)]


# --- cleaning ---


def _clean_latex_author_name(s: str) -> str:
    s = _strip_thanks_blocks(s)
    s = re.sub(r"\$\^\{[^}]*\}\$", "", s)
    s = re.sub(r"\\textsuperscript\s*\{[^}]*\}", "", s)
    s = re.sub(r"\\authornotemark\s*(?:\[[^\]]*\])?", "", s)
    s = re.sub(r"\\[a-zA-Z@]+\s*\{([^}]*)\}", r"\1", s)
    s = s.replace("\\\\", " ").replace("\\", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_address_lines(s: str) -> list[str]:
    t = s.replace("\\\\", "\n")
    lines = re.split(r"[\n]+", t)
    out: list[str] = []
    for line in lines:
        line = re.sub(r"\\textit\s*\{([^}]*)\}", r"\1", line)
        line = re.sub(r"\\[a-zA-Z@]+\s*\{([^}]*)\}", r"\1", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", line):
            continue
        out.append(line)
    return _dedupe_affiliation_strings(out)


def _clean_affil_line(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z@]+\s*\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
