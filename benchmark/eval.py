"""Score docolage.parse() output against the ground truth in benchmark/ground_truth/.

Usage: python benchmark/eval.py [--config-json '{"model_path": "..."}]

For each document in benchmark/manifest.json, runs docolage.parse() and compares
the result against benchmark/ground_truth/<id>.json on two axes:

- text_hit_rate: fraction of `expected_text_contains` snippets found (case-insensitive,
  whitespace-normalized substring match) in the concatenated element content.
- element_count_score: for each type in `expected_min_element_counts` (skipping any
  set to null), min(actual_count / expected_min, 1.0), averaged across types.

Prints a per-document report and an aggregate score. Any parse() failure (e.g. missing
GPU/VLM weights) is reported as an error for that document rather than aborting the run.
"""

import argparse
import json
import re
import sys
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent
PACKAGE_ROOT = BENCHMARK_DIR.parent


def normalize(text: str) -> str:
    # docolage's born-digital text path emits one element per glyph and drops
    # whitespace glyphs entirely, so comparisons must ignore all whitespace.
    return re.sub(r"\s+", "", text).lower()


def text_hit_rate(elements: list[dict], expected_snippets: list[str]) -> tuple[float, list[str]]:
    if not expected_snippets:
        return 1.0, []
    full_text = normalize("".join(el.get("content", "") for el in elements))
    misses = [s for s in expected_snippets if normalize(s) not in full_text]
    hits = len(expected_snippets) - len(misses)
    return hits / len(expected_snippets), misses


def element_count_score(elements: list[dict], expected_min_counts: dict) -> tuple[float, dict]:
    active = {k: v for k, v in expected_min_counts.items() if v is not None}
    if not active:
        return 1.0, {}
    actual_counts = {}
    for el in elements:
        t = el.get("type", "unknown")
        actual_counts[t] = actual_counts.get(t, 0) + 1
    per_type = {}
    for etype, min_count in active.items():
        actual = actual_counts.get(etype, 0)
        score = 1.0 if min_count == 0 else min(actual / min_count, 1.0)
        per_type[etype] = {"expected_min": min_count, "actual": actual, "score": round(score, 2)}
    avg_score = sum(v["score"] for v in per_type.values()) / len(per_type)
    return avg_score, per_type


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-json", default=None, help="JSON-encoded DocolageConfig overrides")
    args = parser.parse_args()

    sys.path.insert(0, str(PACKAGE_ROOT / "src"))
    import docolage
    from docolage.config import DocolageConfig

    config_kwargs = json.loads(args.config_json) if args.config_json else {}
    config = DocolageConfig(**config_kwargs)

    manifest = json.loads((BENCHMARK_DIR / "manifest.json").read_text())

    results = []
    for doc in manifest["documents"]:
        doc_id = doc["id"]
        gt_path = BENCHMARK_DIR / "ground_truth" / f"{doc_id}.json"
        if not gt_path.exists():
            print(f"[{doc_id}] SKIP - no ground truth file at {gt_path}")
            continue
        ground_truth = json.loads(gt_path.read_text())
        doc_path = PACKAGE_ROOT / doc["path"]

        print(f"\n=== {doc_id} ({doc['filename']}) ===")
        try:
            parsed = docolage.parse(str(doc_path), config=config)
        except Exception as exc:  # noqa: BLE001 - report and continue scoring other docs
            print(f"  ERROR during parse(): {exc!r}")
            results.append({"id": doc_id, "error": str(exc)})
            continue

        elements = parsed.get("document", {}).get("elements", [])

        thr, misses = text_hit_rate(elements, ground_truth.get("expected_text_contains", []))
        ecs, per_type = element_count_score(elements, ground_truth.get("expected_min_element_counts", {}))
        overall = round((thr + ecs) / 2, 3)

        print(f"  text_hit_rate: {thr:.2f}" + (f"  (missing: {misses})" if misses else ""))
        print(f"  element_count_score: {ecs:.2f}  {per_type}")
        print(f"  overall: {overall}")

        results.append({
            "id": doc_id,
            "text_hit_rate": round(thr, 3),
            "element_count_score": round(ecs, 3),
            "overall": overall,
        })

    scored = [r for r in results if "overall" in r]
    if scored:
        avg = round(sum(r["overall"] for r in scored) / len(scored), 3)
        print(f"\n=== aggregate over {len(scored)}/{len(results)} documents: {avg} ===")

    errored = [r["id"] for r in results if "error" in r]
    if errored:
        print(f"Documents that failed to parse: {errored}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
