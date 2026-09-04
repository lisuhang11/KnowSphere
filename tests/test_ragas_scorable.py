from evals.runners.ragas_runner import _summarize_ragas, scorable_ragas_rows


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
