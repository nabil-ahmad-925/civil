#!/usr/bin/env python3
"""Scores a processed drawing against a list written by hand (the format is in app/drawings/scoring.py). Reads only.

    python scripts/score_drawing.py <drawing id> expected.json

Run the drawing once with tile_sweep=false and once with tile_sweep=true, and compare: that decides whether the sweep is
worth its cost. Needs the same settings as the server, in the environment or in server/.env. Run from server/.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.deps import get_repo  # noqa: E402
from app.drawings.scoring import score_drawing  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    drawing_id, expected_path = sys.argv[1:]
    expected = json.loads(Path(expected_path).read_text())
    repo = get_repo()
    drawing = repo.get_drawing(drawing_id)
    if not drawing:
        print(f"No drawing {drawing_id}.")
        return 1
    sheets = repo.list_sheets(drawing_id)
    score = score_drawing(expected, sheets, repo.list_drawing_features(drawing_id), repo.list_drawing_facts(drawing_id))
    summary = drawing.summary or {}
    score["tile_sweep"] = summary.get("tile_sweep")
    score["estimated_usd_per_sheet"] = round(summary.get("estimated_usd", 0) / max(len(sheets), 1), 4)
    print(json.dumps(score, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
