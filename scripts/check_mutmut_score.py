#!/usr/bin/env python3
"""Mutation-score gate for CI.

Supports `mutmut results --json` output and a best-effort fallback parser for
plain-text output.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def _parse_json(payload: Any) -> tuple[int, int]:
    if isinstance(payload, dict):
        killed = int(payload.get("killed", 0))
        survived = int(payload.get("survived", 0))
        if killed or survived:
            return killed, survived
        # Some mutmut versions may return nested maps/lists.
        values = payload.values()
    elif isinstance(payload, list):
        values = payload
    else:
        values = []

    killed = 0
    survived = 0
    for item in values:
        if isinstance(item, dict):
            status = str(item.get("status", "")).lower()
            if "killed" in status:
                killed += 1
            elif "survived" in status:
                survived += 1
    return killed, survived


def _parse_text(output: str) -> tuple[int, int]:
    killed = len(re.findall(r"\bkilled\b", output, flags=re.IGNORECASE))
    survived = len(re.findall(r"\bsurvived\b", output, flags=re.IGNORECASE))
    return killed, survived


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=float, default=70.0)
    args = parser.parse_args()

    proc = _run(["mutmut", "results", "--json"])
    killed = survived = 0

    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout or "{}")
            killed, survived = _parse_json(payload)
        except json.JSONDecodeError:
            killed, survived = _parse_text(proc.stdout + "\n" + proc.stderr)
    else:
        fallback = _run(["mutmut", "results"])
        killed, survived = _parse_text(fallback.stdout + "\n" + fallback.stderr)

    total = killed + survived
    score = (100.0 * killed / total) if total else 0.0
    print(f"Mutation score: {score:.2f}% (killed={killed}, survived={survived})")

    return 0 if score >= args.min_score else 1


if __name__ == "__main__":
    raise SystemExit(main())
