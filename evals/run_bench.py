"""rag_bench 评测 CLI（IR + BLEU/ROUGE）。"""

from __future__ import annotations

import argparse
import json

from evals.schemas import EvalConfig
from evals.runners.bench_runner import run_bench, results_to_sample_rows

def main() -> None:
    parser = argparse.ArgumentParser(description="KnowSphere rag_bench 评测")
    parser.add_argument("--dataset", default="campus_demo")
    parser.add_argument("--profile", choices=["rag_fixed", "rag_agent"], default="rag_fixed")
    parser.add_argument("--corpus-mode", choices=["shared", "isolated"], default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    config = EvalConfig(
        dataset_id=args.dataset,
        suite="rag_bench",
        pipeline_profile=args.profile,
        corpus_mode=args.corpus_mode or "shared",
        sample_limit=args.limit,
        workers=args.workers,
    )

    def _prog(done: int, total: int, summary: dict) -> None:
        print(f"[{done}/{total}] 汇总: {json.dumps(summary, ensure_ascii=False)[:120]}...")

    results, summary = run_bench(config, on_progress=_prog)
    print("\n===== rag_bench 汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = "data/bench_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "samples": results_to_sample_rows(results)}, f, ensure_ascii=False, indent=2)
    print(f"明细已写入 {out}")

if __name__ == "__main__":
    main()
