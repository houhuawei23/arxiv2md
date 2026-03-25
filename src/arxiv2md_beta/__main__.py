"""Allow ``python -m arxiv2md_beta`` (delegates to :func:`arxiv2md_beta.cli.main`)."""

from __future__ import annotations

from arxiv2md_beta.cli import main

if __name__ == "__main__":
    main()
