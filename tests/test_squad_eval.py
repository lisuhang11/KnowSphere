"""SQuAD 2.0 数据转换与 EM/F1 指标（不依赖 DB / LLM）。"""

from __future__ import annotations

from evals.config import default_metric_layers
from evals.datasets.squad import convert_squad_hf_rows, convert_squad_official, dataset_from_payload
from evals.metrics.aggregate import average_metrics, compute_sample_metrics, metric_input_from_item
from evals.metrics.squad import (
    exact_match,
    is_abstain,
    normalize_answer,
    token_f1,
)
from evals.pipelines.agent import eval_system_prompt
from evals.schemas import SampleMetrics, SampleResult, SquadMetrics

_MINI_OFFICIAL = {
    "data": [
        {
            "title": "Normans",
            "paragraphs": [
                {
                    "context": "The Normans were the people who in the 10th and 11th centuries gave their name to Normandy, a region in France.",
                    "qas": [
                        {
                            "id": "n1",
                            "question": "In what country is Normandy located?",
                            "answers": [
                                {"text": "France", "answer_start": 110},
                                {"text": "France", "answer_start": 110},
                            ],
                            "is_impossible": False,
                        },
                        {
                            "id": "n2",
                            "question": "What is France a region of?",
                            "answers": [],
                            "is_impossible": True,
                        },
                    ],
                },
                {
                    "context": "The Duchy of Normandy was established in 911.",
                    "qas": [
                        {
                            "id": "n3",
                            "question": "When was the Duchy of Normandy founded?",
                            "answers": [{"text": "911", "answer_start": 41}],
                            "is_impossible": False,
                        }
                    ],
                },
            ],
        }
    ]
}


def test_normalize_and_em_f1_match_official_script():
    assert normalize_answer("The France.") == "france"
    assert exact_match("France", "france") == 1.0
    assert exact_match("France", "Normandy is in France") == 0.0
    assert token_f1("France", "France") == 1.0
    assert token_f1("", "") == 1.0
    assert token_f1("", "France") == 0.0
    assert token_f1("10th and 11th centuries", "10th and 11th centuries") == 1.0


def test_abstain_detection():
    assert is_abstain("")
    assert is_abstain("unanswerable")
    assert is_abstain("Unanswerable.")
    assert is_abstain("I don't know")
    assert is_abstain("The passages do not contain the answer.")
    assert is_abstain("无法回答")
    assert is_abstain("未找到相关信息")
    assert not is_abstain("France")
    assert not is_abstain("Normandy is located in France")


def test_convert_official_normans_mini():
    payload = convert_squad_official(_MINI_OFFICIAL, dataset_id="squad_normans")
    assert len(payload["passages"]) == 2
    assert len(payload["items"]) == 3
    has_ans = [row for row in payload["items"] if not row["meta"]["is_impossible"]]
    no_ans = [row for row in payload["items"] if row["meta"]["is_impossible"]]
    assert len(has_ans) == 2
    assert len(no_ans) == 1
    assert has_ans[0]["answer"] == "France"
    assert has_ans[0]["pids"] == [0]
    assert no_ans[0]["answer"] == ""
    ds = dataset_from_payload(payload)
    assert ds.id == "squad_normans"
    assert ds.items[1].meta["is_impossible"] is True
    assert "Normans" in payload["description"]
    assert payload["source"] == "squad_v2:Normans"


def test_convert_hf_rows_numpy_answers():
    class _Arr(list):
        def __bool__(self):  # pragma: no cover - 模拟 numpy 数组
            raise ValueError("ambiguous")

    rows = [
        {
            "id": "a",
            "title": "Normans",
            "context": "ctx",
            "question": "q",
            "answers": {"text": _Arr(["France", "France"]), "answer_start": _Arr([0, 0])},
        }
    ]
    payload = convert_squad_hf_rows(rows, dataset_id="squad_mini")
    assert payload["items"][0]["answer"] == "France"


def test_convert_hf_rows_groups_shared_context():
    rows = [
        {
            "id": "a",
            "title": "Normans",
            "context": "ctx-one",
            "question": "q1",
            "answers": {"text": ["France"], "answer_start": [0]},
        },
        {
            "id": "b",
            "title": "Normans",
            "context": "ctx-one",
            "question": "q2",
            "answers": {"text": [], "answer_start": []},
        },
        {
            "id": "c",
            "title": "Normans",
            "context": "ctx-two",
            "question": "q3",
            "answers": {"text": ["911"], "answer_start": [0]},
        },
    ]
    payload = convert_squad_hf_rows(rows, dataset_id="squad_v2")
    assert len(payload["passages"]) == 2
    assert payload["items"][0]["pids"] == payload["items"][1]["pids"] == [0]
    assert payload["items"][1]["meta"]["is_impossible"] is True
    assert payload["items"][2]["pids"] == [1]


def test_squad_metrics_has_ans_and_no_ans():
    payload = convert_squad_official(_MINI_OFFICIAL, dataset_id="squad_mini")
    ds = dataset_from_payload(payload)
    has_item = ds.items[0]
    no_item = ds.items[1]

    exact = compute_sample_metrics(
        metric_input_from_item(has_item, generated_text="France", retrieval_ids=[0]),
        layers=["squad"],
    ).squad
    assert exact is not None
    assert exact.em == 1.0
    assert exact.f1 == 1.0
    assert exact.span_hit == 1.0
    assert exact.abstained == 0.0

    verbose = compute_sample_metrics(
        metric_input_from_item(has_item, generated_text="Normandy is located in France.", retrieval_ids=[0]),
        layers=["squad"],
    ).squad
    assert verbose is not None
    assert verbose.em == 0.0
    assert verbose.span_hit == 1.0
    assert verbose.f1 > 0

    abstain_ok = compute_sample_metrics(
        metric_input_from_item(no_item, generated_text="unanswerable", retrieval_ids=[0]),
        layers=["squad"],
    ).squad
    assert abstain_ok is not None
    assert abstain_ok.em == 1.0
    assert abstain_ok.impossible == 1.0

    hallucinated = compute_sample_metrics(
        metric_input_from_item(no_item, generated_text="Germany", retrieval_ids=[0]),
        layers=["squad"],
    ).squad
    assert hallucinated is not None
    assert hallucinated.em == 0.0
    assert hallucinated.f1 == 0.0


def test_aggregate_squad_splits_has_ans_no_ans():
    results = [
        SampleResult(
            qid=0,
            question="q",
            reference="France",
            response="France",
            retrieval_ids=[0],
            retrieval_gt=[0],
            metrics=SampleMetrics(squad=SquadMetrics(em=1, f1=1, span_hit=1, abstained=0, impossible=0)),
        ),
        SampleResult(
            qid=1,
            question="q",
            reference="",
            response="Germany",
            retrieval_ids=[0],
            retrieval_gt=[0],
            metrics=SampleMetrics(squad=SquadMetrics(em=0, f1=0, span_hit=0, abstained=0, impossible=1)),
        ),
    ]
    summary = average_metrics(results)
    sm = summary["squad_metrics"]
    assert sm["has_ans_count"] == 1
    assert sm["no_ans_count"] == 1
    assert sm["has_ans_em"] == 1.0
    assert sm["no_ans_acc"] == 0.0
    assert sm["em"] == 0.5


def test_default_metric_layers_and_prompt():
    assert default_metric_layers("rag_bench", "squad_normans") == ["retrieval", "squad"]
    assert default_metric_layers("rag_bench", "campus_demo") == ["retrieval", "generation"]
    prompt = eval_system_prompt(["retrieval", "squad"])
    assert "unanswerable" not in prompt
    assert "Evidence-First" in prompt
    assert "doc_retrieval" in prompt
    assert "unanswerable" not in eval_system_prompt(["retrieval", "generation"])


def test_list_datasets_includes_squad_v2():
    from evals.datasets import list_datasets, load_dataset

    ids = {d["id"] for d in list_datasets()}
    assert "squad_v2" in ids
    assert "hotpot" in ids
    ds = load_dataset("squad_normans", sample_limit=3)
    assert ds.id == "squad_normans"
    # 按段整抽：题数可能略超预算，但同一 pid 的题必须全部保留
    assert len(ds.items) >= 3
    assert ds.passages
    keep_pids = {p.pid for p in ds.passages}
    assert {pid for it in ds.items for pid in it.pids} == keep_pids
    # 源数据中属于这些 pid 的题应全部在结果里（不半段截断）
    full = load_dataset("squad_normans")
    for pid in keep_pids:
        n_full = sum(1 for it in full.items if pid in it.pids)
        n_samp = sum(1 for it in ds.items if pid in it.pids)
        assert n_samp == n_full
