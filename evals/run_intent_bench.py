"""intent_bench 评测 CLI。"""

from __future__ import annotations

import argparse
import json

from evals.runners.intent_runner import results_to_sample_rows, run_intent_bench
from evals.schemas import EvalConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="KnowSphere intent_bench 评测")
    parser.add_argument("--dataset", default="intent_demo")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    config = EvalConfig(
        dataset_id=args.dataset,
        suite="intent_bench",
        pipeline_profile="intent",
        sample_limit=args.limit,
        workers=args.workers,
        metric_layers=["intent"],
    )

    def _prog(done: int, total: int, summary: dict) -> None:
        print(f"[{done}/{total}] 汇总: {json.dumps(summary, ensure_ascii=False)[:160]}...")

    results, summary = run_intent_bench(config, on_progress=_prog)
    print("\n===== intent_bench 汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = "data/intent_bench_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {"summary": summary, "samples": results_to_sample_rows(results)},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"明细已写入 {out}")


if __name__ == "__main__":
    main()
