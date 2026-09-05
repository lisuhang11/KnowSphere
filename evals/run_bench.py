"""rag_bench 评测 CLI（IR + BLEU/ROUGE，或 SQuAD EM/F1）。"""

from __future__ import annotations

import argparse
import json

from evals.config import default_metric_layers
from evals.runners.bench_runner import results_to_sample_rows, run_bench
from evals.schemas import EvalConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="KnowSphere rag_bench 评测")
    parser.add_argument("--dataset", default="campus_demo")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--metrics",
        default=None,
        help="逗号分隔指标层，如 retrieval,squad；缺省按数据集选择",
    )
    args = parser.parse_args()

    layers = [m.strip() for m in args.metrics.split(",") if m.strip()] if args.metrics else default_metric_layers(
        "rag_bench", args.dataset
    )
    config = EvalConfig(
        dataset_id=args.dataset,
        suite="rag_bench",
        pipeline_profile="rag_agent",
        sample_limit=args.limit,
        metric_layers=layers,
        workers=args.workers,
    )

    def _prog(done: int, total: int, summary: dict) -> None:
        print(f"[{done}/{total}] 汇总: {json.dumps(summary, ensure_ascii=False)[:160]}...")

    results, summary = run_bench(config, on_progress=_prog)
    print("\n===== rag_bench 汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = "data/bench_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "samples": results_to_sample_rows(results)}, f, ensure_ascii=False, indent=2)
    print(f"明细已写入 {out}")


if __name__ == "__main__":
    main()
