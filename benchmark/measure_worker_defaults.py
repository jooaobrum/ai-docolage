"""Measure docling's per-page latency and recommend Service defaults.

Ticket #25 ("Real-GPU worker image and measured operational defaults") and
spec section "Unresolved, pending measurement": the Sync gate (max pages
accepted on the Sync path) and the sync stream timeout are both functions
of docling's measured per-page latency. This script is the "benchmark run
over the existing sample documents" the spec asks for, made reproducible
rather than a one-off.

It drives ``DoclingRecognizer.recognize()`` directly, once per rasterized
page, over every PDF in ``data/`` (the same sample set ``benchmark/eval.py``
and ``benchmark/manifest.json`` use) and reports:

- per-page latency (mean, p50, p95, max) across all pages of all documents
- a recommended ``sync_gate_max_pages`` (how many pages fit in a target
  sync wall-clock budget)
- a recommended ``sync_stream_timeout_seconds`` (gate pages x measured
  per-page latency, plus margin)

IMPORTANT: whatever hardware this runs on, the numbers are only as good as
that hardware. Running this on a CPU-only box (the common case for a
laptop/CI sandbox) produces CPU numbers — real, not guessed, but *not* GPU
numbers. Re-run this script on the target GPU host before trusting the
defaults in production; see docs/operations.md.

Usage:
    python benchmark/measure_worker_defaults.py [--target-seconds 45] [--margin 2.0]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent
PACKAGE_ROOT = BENCHMARK_DIR.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-seconds",
        type=float,
        default=45.0,
        help="Wall-clock budget a sync request should complete within (default: 45s).",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=2.0,
        help="Multiplier applied to (gate_pages * mean_latency) to get the stream timeout (default: 2x).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write the measurement JSON (default: benchmark/worker_defaults.json).",
    )
    parser.add_argument(
        "--max-pages-per-doc",
        type=int,
        default=8,
        help=(
            "Cap pages measured per document (default: 8). Sample set includes a "
            "469-page report; per-page latency is what matters, not full-document "
            "coverage, so a small representative sample per doc keeps this fast."
        ),
    )
    args = parser.parse_args()

    sys.path.insert(0, str(PACKAGE_ROOT / "src"))

    import pypdfium2 as pdfium

    from docolage.rasterize import rasterize_page
    from docolage.recognizer_docling import DoclingRecognizer

    manifest = json.loads((BENCHMARK_DIR / "manifest.json").read_text())

    print("Loading docling models (paid once, like a Worker process at startup)...")
    load_start = time.perf_counter()
    recognizer = DoclingRecognizer()
    load_seconds = time.perf_counter() - load_start
    print(f"  model load: {load_seconds:.2f}s\n")

    per_page_seconds: list[float] = []
    per_doc_summary: list[dict] = []

    import tempfile

    with tempfile.TemporaryDirectory(prefix="docolage-benchmark-") as tmpdir:
        out_dir = Path(tmpdir)

        for doc in manifest["documents"]:
            doc_path = PACKAGE_ROOT / doc["path"]
            if not doc_path.exists():
                print(f"[{doc['id']}] SKIP - file not found at {doc_path}")
                continue

            pdf = pdfium.PdfDocument(str(doc_path))
            try:
                n_pages = len(pdf)
                pages_to_measure = min(n_pages, args.max_pages_per_doc)
                doc_times: list[float] = []
                for page_index in range(pages_to_measure):
                    image_path = rasterize_page(pdf, page_index, out_dir, doc["id"], 200)
                    start = time.perf_counter()
                    recognizer.recognize(image_path, page_index + 1)
                    elapsed = time.perf_counter() - start
                    doc_times.append(elapsed)
                    per_page_seconds.append(elapsed)
                    print(f"[{doc['id']}] page {page_index + 1}/{pages_to_measure} (of {n_pages}): {elapsed:.2f}s")
            finally:
                pdf.close()

            per_doc_summary.append(
                {
                    "id": doc["id"],
                    "total_pages": n_pages,
                    "pages_measured": pages_to_measure,
                    "mean_seconds": round(statistics.mean(doc_times), 3) if doc_times else None,
                }
            )

    if not per_page_seconds:
        print("No pages measured (no sample PDFs found) - cannot recommend defaults.")
        return 1

    mean_s = statistics.mean(per_page_seconds)
    p50_s = statistics.median(per_page_seconds)
    p95_s = statistics.quantiles(per_page_seconds, n=20)[18] if len(per_page_seconds) >= 20 else max(per_page_seconds)
    max_s = max(per_page_seconds)

    recommended_gate_pages = max(1, int(args.target_seconds // mean_s))
    recommended_timeout_s = round(recommended_gate_pages * mean_s * args.margin, 1)

    report = {
        "hardware": "CPU (see script docstring - re-run on target GPU host for production defaults)",
        "model_load_seconds": round(load_seconds, 3),
        "pages_measured": len(per_page_seconds),
        "per_page_latency_seconds": {
            "mean": round(mean_s, 3),
            "p50": round(p50_s, 3),
            "p95": round(p95_s, 3),
            "max": round(max_s, 3),
        },
        "per_document": per_doc_summary,
        "target_sync_seconds": args.target_seconds,
        "timeout_margin_multiplier": args.margin,
        "recommended_defaults": {
            "sync_gate_max_pages": recommended_gate_pages,
            "sync_stream_timeout_seconds": recommended_timeout_s,
        },
    }

    print("\n=== measured per-page latency (seconds) ===")
    print(json.dumps(report["per_page_latency_seconds"], indent=2))
    print("\n=== recommended defaults ===")
    print(json.dumps(report["recommended_defaults"], indent=2))

    out_path = Path(args.out) if args.out else BENCHMARK_DIR / "worker_defaults.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nWrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
