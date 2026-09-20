"""OpenJev Choice：从 logprobs 读选项概率。"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from models.decision import (
    ChoiceResult,
    classify_choice,
    extract_top_logprobs,
    softmax,
)
from schemas.query import INTENT_CHOICE_OPTIONS


def test_softmax_normalizes_and_is_stable():
    probs = softmax({"a": 1.0, "b": 1.0})
    assert abs(probs["a"] - 0.5) < 1e-9
    assert abs(probs["b"] - 0.5) < 1e-9
    huge = softmax({"a": 1000.0, "b": 1001.0})
    assert huge["b"] > huge["a"]
    assert abs(sum(huge.values()) - 1.0) < 1e-9


def test_extract_top_logprobs_from_openai_metadata():
    msg = AIMessage(
        content="D",
        response_metadata={
            "logprobs": {
                "content": [
                    {
                        "token": "D",
                        "logprob": -0.1,
                        "top_logprobs": [
                            {"token": "D", "logprob": -0.1},
                            {"token": " C", "logprob": -2.3},
                        ],
                    }
                ]
            }
        },
    )
    top = extract_top_logprobs(msg)
    assert top is not None
    assert top[0]["token"] == "D"


def test_classify_choice_reads_letter_logprobs():
    msg = AIMessage(
        content="D",
        response_metadata={
            "logprobs": {
                "content": [
                    {
                        "token": "D",
                        "logprob": -0.05,
                        "top_logprobs": [
                            {"token": "D", "logprob": -0.05},
                            {"token": "C", "logprob": -3.0},
                            {"token": "A", "logprob": -4.0},
                        ],
                    }
                ]
            }
        },
    )
    llm = MagicMock()
    llm.bind.return_value.invoke.return_value = msg
    result = classify_choice(llm, [{"role": "user", "content": "x"}], INTENT_CHOICE_OPTIONS)
    assert isinstance(result, ChoiceResult)
    assert result.label == "kb_search"
    assert result.letter == "D"
    assert result.probs["kb_search"] > result.probs["web_search"]
    assert result.confidence == result.probs["kb_search"]
    llm.bind.assert_called()
    kwargs = llm.bind.call_args.kwargs
    assert kwargs["logprobs"] is True
    assert kwargs["max_tokens"] == 8


def test_classify_choice_parses_generated_label_without_logprobs():
    llm = MagicMock()
    llm.bind.return_value.invoke.return_value = AIMessage(content="greeting")
    result = classify_choice(llm, [{"role": "user", "content": "你好"}], INTENT_CHOICE_OPTIONS)
    assert result is not None
    assert result.label == "greeting"
    assert result.probs["greeting"] == 1.0


def test_classify_choice_strips_backticks_around_letter():
    llm = MagicMock()
    llm.bind.return_value.invoke.return_value = AIMessage(content="`D`")
    result = classify_choice(llm, [{"role": "user", "content": "x"}], INTENT_CHOICE_OPTIONS)
    assert result is not None
    assert result.label == "kb_search"


def test_classify_choice_retries_when_logprobs_unsupported():
    llm = MagicMock()
    bound_logprobs = MagicMock()
    bound_logprobs.invoke.side_effect = RuntimeError("top_logprobs is not supported")
    bound_plain = MagicMock()
    bound_plain.invoke.return_value = AIMessage(content="A")

    def fake_bind(**kwargs):
        if kwargs.get("logprobs"):
            return bound_logprobs
        return bound_plain

    llm.bind.side_effect = fake_bind
    result = classify_choice(llm, [{"role": "user", "content": "hi"}], INTENT_CHOICE_OPTIONS)
    assert result is not None
    assert result.label == "greeting"


def test_classify_choice_returns_none_for_garbage():
    llm = MagicMock()
    llm.bind.return_value.invoke.return_value = AIMessage(content="???")
    assert classify_choice(llm, [{"role": "user", "content": "x"}], INTENT_CHOICE_OPTIONS) is None
