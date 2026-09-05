from evals.runners.ragas_runner import (
    _finite_scores,
    _ragas_sample_payload,
    _strip_think,
    _summarize_ragas,
    samples_to_ragas_rows,
    scorable_ragas_rows,
)


def test_scorable_ragas_rows_skips_errors_and_empty_answers():
    rows = [
        {"qid": 0, "error": "Error code: 429", "user_input": "Inflammation?", "response": ""},
        {"qid": 1, "user_input": "What is packet switching?", "response": "Davies named it packet switching."},
        {"qid": 2, "user_input": " ", "response": "no question"},
        {"qid": 3, "user_input": "ok", "response": "   "},
        {
            "qid": 4,
            "user_input": "Who backed the policies?",
            "response": "congresses and presidents",
            "retrieved_contexts": "not-a-list",
            "reference": None,
        },
    ]
    ok = scorable_ragas_rows(rows)
    assert [r["qid"] for r in ok] == [1, 4]
    assert ok[1]["retrieved_contexts"] == []
    assert ok[1]["reference"] == ""


def test_samples_to_ragas_rows_uses_collected_trace():
    samples = [
        {
            "qid": 0,
            "error": "429",
            "question": "fail",
            "response": "",
        },
        {
            "qid": 1,
            "question": "What did Davies call his system?",
            "response": "packet switching",
            "reference": "packet switching",
            "details": {"retrieved_contexts": ["Davies named it packet switching."]},
        },
        {
            "qid": 2,
            "question": "empty answer",
            "response": "  ",
        },
    ]
    rows = samples_to_ragas_rows(samples)
    assert len(rows) == 1
    assert rows[0]["user_input"] == "What did Davies call his system?"
    assert rows[0]["retrieved_contexts"] == ["Davies named it packet switching."]
    assert rows[0]["reference"] == "packet switching"


def test_summarize_ragas_averages_only_scored_items():
    summary = _summarize_ragas(
        {
            0: {"faithfulness": 1.0, "answer_relevancy": 0.5},
            1: {},
            2: {"faithfulness": 0.5, "answer_relevancy": 0.5},
        }
    )
    assert summary["faithfulness"] == 0.75
    assert summary["answer_relevancy"] == 0.5


def test_ragas_sample_payload_drops_qid_and_caps_contexts():
    payload = _ragas_sample_payload(
        {
            "qid": 7,
            "user_input": "Who were the Semuren?",
            "response": "allied groups",
            "retrieved_contexts": [f"c{i}" for i in range(20)],
            "reference": "allied groups from Central Asia",
            "error": None,
        }
    )
    assert "qid" not in payload
    assert payload["user_input"] == "Who were the Semuren?"
    assert payload["retrieved_contexts"] == [f"c{i}" for i in range(8)]
    assert payload["reference"].startswith("allied groups")


def test_strip_think_removes_qwen_blocks():
    raw = "<think>先分析题面</think>\n{\"verdict\": 1}"
    assert _strip_think(raw) == '{"verdict": 1}'


def test_finite_scores_ignores_nan_and_trace_columns():
    scores = _finite_scores(
        {
            "qid": 0,
            "user_input": "q",
            "faithfulness": 0.8,
            "answer_relevancy": float("nan"),
            "context_precision": "0.25",
            "error": "boom",
        }
    )
    assert scores == {"faithfulness": 0.8, "context_precision": 0.25}
