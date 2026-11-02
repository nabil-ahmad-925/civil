"""How well the reader did on a drawing, against a list written by hand from the real sheet.

The expected list is JSON, by sheet number:

    {"sheets": {"1": {
        "features": [{"type": "bridge"}, {"type": "u_turn"}],
        "values": [{"feature": "u_turn", "term": "turning_radius", "text": "R = 50 FT"}]
    }}}

"feature" and "term" are optional on a value. The number to push to zero is `values_wrong_confident`: a value that was read
differently from what is printed, and that the second read did not catch. That one reaches a person looking correct.
"""

from collections import Counter
from typing import Any

from ..models import DrawingFact, DrawingFeature, DrawingSheet
from .reader import _normalized


def score_drawing(
    expected: dict[str, Any], sheets: list[DrawingSheet], features: list[DrawingFeature], facts: list[DrawingFact]
) -> dict[str, Any]:
    number = {s.id: str(s.number) for s in sheets}
    kind = {f.id: f.type for f in features}
    out = Counter(
        features_expected=0, features_found=0, features_missed=0, features_extra=0,
        values_expected=0, values_correct=0, values_wrong_confident=0, values_wrong_flagged=0, values_missed=0,
    )
    missed: list[str] = []
    wrong: list[str] = []
    for sheet_number in {str(s.number) for s in sheets} | set(expected.get("sheets", {})):
        want = expected.get("sheets", {}).get(sheet_number, {})
        have_features = Counter(f.type for f in features if number.get(f.sheet_id) == sheet_number)
        want_features = Counter(w["type"] for w in want.get("features", []))
        out["features_expected"] += sum(want_features.values())
        out["features_found"] += sum((want_features & have_features).values())
        out["features_missed"] += sum((want_features - have_features).values())
        out["features_extra"] += sum((have_features - want_features).values())
        missed += [f"sheet {sheet_number}: {t}" for t in (want_features - have_features).elements()]

        found = [f for f in facts if number.get(f.sheet_id) == sheet_number and f.status == "FOUND"]
        for w in want.get("values", []):
            out["values_expected"] += 1
            pool = [f for f in found if "feature" not in w or kind.get(f.feature_id or "") == w["feature"]]
            text = _normalized(w["text"])
            if any(_normalized(f.verbatim_text) == text for f in pool):
                out["values_correct"] += 1
                continue
            rivals = [f for f in pool if "term" in w and f.term == w["term"]]
            if any(f.confirmation == "confirmed" and f.confidence != "low" for f in rivals):
                out["values_wrong_confident"] += 1
                wrong.append(f"sheet {sheet_number}: expected '{w['text']}', read '{rivals[0].verbatim_text}'")
            elif rivals:
                out["values_wrong_flagged"] += 1
                wrong.append(f"sheet {sheet_number}: expected '{w['text']}', read '{rivals[0].verbatim_text}' (flagged)")
            else:
                out["values_missed"] += 1
                missed.append(f"sheet {sheet_number}: value '{w['text']}'")
    not_found = sum(1 for f in facts if f.status == "NOT_FOUND")
    return {
        **out,
        "not_found_rate": round(not_found / len(facts), 3) if facts else 0.0,
        "missed": sorted(missed),
        "wrong": sorted(wrong),
    }
