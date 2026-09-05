"""RAGAS 评测 CLI（HotpotQA）。"""

from __future__ import annotations

import argparse

import pandas as pd

from evals.runners.ragas_runner import run_ragas_eval

def main() -> None:
    parser = argparse.ArgumentParser(description="HotpotQA + RAGAS 评测")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--split", type=str, default="validation")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    def _prog(done: int, total: int) -> None:
        print(f"[{done}/{total}] 跑题进度")

    _, _, samples = run_ragas_eval(
        n=args.n,
        seed=args.seed,
        split=args.split,
        workers=args.workers,
        on_progress=_prog,
    )
    from evals.runners.ragas_runner import _run_ragas_batch, samples_to_ragas_rows

    print(f"已收集 {len(samples)} 题，开始离线 RAGAS…")
    summary, detail, _ = _run_ragas_batch(samples_to_ragas_rows(samples))
    print("\n===== RAGAS 汇总（均值）=====")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    out = "data/ragas_report.csv"
    pd.DataFrame(detail).to_csv(out, index=False)
    print(f"逐题明细已写入 {out}")

if __name__ == "__main__":
    main()
