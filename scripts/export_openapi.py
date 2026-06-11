"""Export the live FastAPI OpenAPI schema to docs/openapi.yaml.

Usage:
    MASTER_API_KEY=any python scripts/export_openapi.py

CI check (fails if routes changed without regenerating):
    python scripts/export_openapi.py && git diff --exit-code docs/openapi.yaml
"""

import os
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

# MASTER_API_KEY must be set for Settings() to instantiate.
os.environ.setdefault("MASTER_API_KEY", "export-placeholder-not-used-at-runtime")

import yaml  # noqa: E402
from api.main import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "openapi.yaml"


def main() -> None:
    schema = create_app().openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Write UTF-8 explicitly — on Windows the default encoding is cp1252, which
    # would corrupt any non-ASCII description and break read_text() on Linux/CI.
    with open(OUT, "w", encoding="utf-8") as fh:
        yaml.dump(schema, fh, allow_unicode=True, sort_keys=False)
    print(f"Written {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
