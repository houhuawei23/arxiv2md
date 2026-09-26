"""Single source of the structured-export ``SCHEMA_VERSION``.

The structured export itself is produced by
:mod:`arxiv2md_beta.ir.emitters.json_emitter`, whose authoritative types are
the Pydantic IR models in :mod:`arxiv2md_beta.ir` (the emitter builds its
JSON dicts directly from them). The former coarse ``*Json`` model set here
described a shape the emitter never emitted and was removed.
"""

from __future__ import annotations

SCHEMA_VERSION = "2.0"
