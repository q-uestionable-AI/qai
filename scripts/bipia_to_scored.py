#!/usr/bin/env python3
"""Convert BIPIA (KDD 2025) benchmark data to scored-prompts JSON.

This is a standalone reference implementation — it is NOT installed with the
qai package.  It demonstrates how to convert an external benchmark dataset
into the canonical scored-prompts format consumed by ``qai import --format scored``.

Usage
-----
From HuggingFace (requires ``datasets`` package)::

    python scripts/bipia_to_scored.py --hf --output bipia_scored.json

From a local CSV::

    python scripts/bipia_to_scored.py --csv bipia_results.csv --output bipia_scored.json

CSV format
----------
The local CSV must contain at least these columns:

- ``category``   - attack category (e.g. ``instruction_override``)
- ``prompt``     - the user prompt sent to the model
- ``response``   - the model's response text
- ``complied``   - ``true`` / ``false`` indicating whether the model complied

Optional columns: ``probe_id``, ``description``, ``score``, ``model``.

Network access
--------------
The ``--hf`` flag downloads the BIPIA dataset from HuggingFace.  Use
``--csv`` with a pre-downloaded file to avoid network access entirely.

Requirements
------------
- ``datasets`` (only for ``--hf`` mode)
- Python >= 3.11
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_BIPIA_HF_DATASET = "AI-Secure/BIPIA"
_SOURCE_NAME = "bipia"
_FORMAT_VERSION = "1.0"


def _parse_bool(value: str) -> bool:
    """Parse a boolean string from CSV."""
    return value.strip().lower() in ("true", "1", "yes")


def _load_from_csv(csv_path: Path) -> list[dict]:
    """Load benchmark entries from a local CSV file."""
    entries: list[dict] = []
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            complied = _parse_bool(row.get("complied", "false"))
            try:
                score = float(row["score"])
            except (KeyError, ValueError):
                score = 1.0 if complied else 0.0

            entries.append(
                {
                    "probe_id": row.get("probe_id", ""),
                    "category": row.get("category", "unknown"),
                    "description": row.get("description", ""),
                    "system_prompt": row.get("system_prompt", ""),
                    "user_prompt": row.get("prompt", ""),
                    "canary": "",
                    "response_text": row.get("response", ""),
                    "complied": complied,
                    "score": score,
                    "error": None,
                }
            )
    return entries


def _load_from_hf(split: str = "test") -> list[dict]:
    """Load BIPIA dataset from HuggingFace.

    Requires the ``datasets`` package.
    """
    try:
        from datasets import load_dataset  # type: ignore[import-untyped]
    except ImportError:
        print(  # noqa: T201
            "Error: the 'datasets' package is required for --hf mode.\n"
            "Install with: pip install datasets",
            file=sys.stderr,
        )
        sys.exit(1)

    ds = load_dataset(_BIPIA_HF_DATASET, split=split)

    entries: list[dict] = []
    for idx, row in enumerate(ds):
        complied = bool(row.get("complied", False))
        try:
            score = float(row.get("score", 1.0 if complied else 0.0))
        except (TypeError, ValueError):
            score = 1.0 if complied else 0.0

        entries.append(
            {
                "probe_id": row.get("id", f"bipia-{idx}"),
                "category": row.get("attack_type", row.get("category", "unknown")),
                "description": row.get("description", ""),
                "system_prompt": row.get("context", ""),
                "user_prompt": row.get("prompt", row.get("question", "")),
                "canary": "",
                "response_text": row.get("response", row.get("answer", "")),
                "complied": complied,
                "score": score,
                "error": None,
            }
        )
    return entries


def _build_scored_json(
    entries: list[dict],
    model: str,
) -> dict:
    """Build a scored-prompts JSON structure from entries."""
    total = len(entries)
    complied_count = sum(1 for e in entries if e.get("complied"))
    rate = complied_count / total if total > 0 else 0.0

    # Per-category summary.
    categories: dict[str, dict[str, int]] = {}
    for e in entries:
        cat = e.get("category", "unknown")
        if cat not in categories:
            categories[cat] = {"total": 0, "complied": 0}
        categories[cat]["total"] += 1
        if e.get("complied"):
            categories[cat]["complied"] += 1

    category_summary = {}
    for cat, stats in categories.items():
        cat_rate = stats["complied"] / stats["total"] if stats["total"] > 0 else 0.0
        category_summary[cat] = {
            "total": stats["total"],
            "complied": stats["complied"],
            "rate": cat_rate,
        }

    return {
        "format": "scored-prompts",
        "version": _FORMAT_VERSION,
        "source": _SOURCE_NAME,
        "model": model,
        "total_probes": total,
        "total_complied": complied_count,
        "overall_compliance_rate": rate,
        "category_summary": category_summary,
        "results": entries,
    }


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Convert BIPIA benchmark data to scored-prompts JSON.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--hf",
        action="store_true",
        help="Load from HuggingFace (requires 'datasets' package and network access).",
    )
    source.add_argument(
        "--csv",
        type=Path,
        help="Path to a local BIPIA CSV file.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Output path for the scored-prompts JSON.",
    )
    parser.add_argument(
        "--model",
        default="unknown",
        help="Model name to record in the output (default: unknown).",
    )
    parser.add_argument(
        "--split",
        default="test",
        help="HuggingFace dataset split to load (default: test).",
    )
    args = parser.parse_args()

    if args.hf:
        print(f"Loading BIPIA dataset from HuggingFace ({args.split} split)...")  # noqa: T201
        entries = _load_from_hf(split=args.split)
    else:
        if not args.csv.exists():
            print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)  # noqa: T201
            sys.exit(1)
        print(f"Loading from CSV: {args.csv}")  # noqa: T201
        entries = _load_from_csv(args.csv)

    print(f"Loaded {len(entries)} entries")  # noqa: T201

    scored = _build_scored_json(entries, model=args.model)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scored, indent=2, default=str), encoding="utf-8")
    print(f"Wrote scored-prompts JSON to: {args.output}")  # noqa: T201


if __name__ == "__main__":
    main()
