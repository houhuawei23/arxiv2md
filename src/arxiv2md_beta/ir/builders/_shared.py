"""Constants shared by the HTML and LaTeX builders.

Citation-anchor conventions (same shapes both builders must match): a
bibliography anchor is ``#bib.bibN`` (ar5iv) and the sidecar/reference
anchors emitted downstream are ``ref-N`` / ``#ref-N``. Anything else
starting with ``#`` is an ordinary internal link (audit5 G1-4).
"""

from __future__ import annotations

import re

BIB_REF_RE = re.compile(r"#bib\.bib(\d+)")
REF_FRAGMENT_RE = re.compile(r"#ref-\d+")
