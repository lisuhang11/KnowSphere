"""官方 OpenJEV Choice：解析响应，并在配了 Key 时接管意图分类。"""

from unittest.mock import MagicMock, patch

import httpx

from models.decision import ChoiceResult
from models.openjev import classify_openjev, parse_choice_answer
from prompts.intent_choice import INTENT_OPENJEV_CRITERIA


def test_parse_choice_answer_reads_label_and_probabilities():
    result = parse_choice_answer(
        {
            "answers": {
                "intent": {
                    "type": "choice",
                    "choice": "kb_search",
                    "probabilities": {"kb_search": 0.8, "follow_up": 0.2},
                    "confidence": 0.71,
                }
            }
        },
        question_id="intent",
        labels=tuple(INTENT_OPENJEV_CRITERIA),
    )
    assert result is not None
    assert result.label == "kb_search"
    assert result.confidence == 0.71
    assert result.probs["kb_search"] == 0.8
    assert result.probs["greeting"] == 0.0


def test_parse_choice_answer_rejects_unknown_label():
    assert (
        parse_choice_answer(
            {"answers": {"intent": {"choice": "no_kb"}}},
            question_id="intent",
            labels=("kb_search",),
        )
        is None
    )


def test_classify_openjev_posts_systemone():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/systemone"
        assert request.headers["authorization"] == "Bearer test-key"
        body = request.read()
        assert b'"type":"choice"' in body or b'"type": "choice"' in body
        return httpx.Response(
            200,
            json={
                "model": "openjev",
                "answers": {
                    "intent": {
                        "type": "choice",
                        "choice": "greeting",
                        "probabilities": {"greeting": 0.96, "chitchat": 0.04},
                        "confidence": 0.9,
                    }
                },
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with patch("models.openjev.httpx.Client", side_effect=client_factory):
        result = classify_openjev(
            state={"query": "你好"},
            criteria={"greeting": "hi", "chitchat": "chat"},
            instructions="Classify the intent.",
            api_key="test-key",
            base_url="https://api.openjev.sh",
        )

    assert result is not None
    assert result.label == "greeting"
    assert result.confidence == 0.9


def test_query_understand_prefers_openjev_over_logprobs():
    from agents.nodes.query_understand import query_understand

    state = {
        "current_query": "你好",
        "history_pairs": [],
        "kb_selected": True,
    }
    hosted = ChoiceResult(
        label="greeting",
        letter="",
        probs={"greeting": 0.93, "chitchat": 0.07},
        confidence=0.88,
    )
    mock_llm = MagicMock()

    with (
        patch("agents.nodes.query_understand.settings") as mock_settings,
        patch("agents.nodes.query_understand.create_chat_model", return_value=mock_llm),
        patch("agents.nodes.query_understand.classify_openjev", return_value=hosted) as hosted_call,
    ):
        mock_settings.enable_rewrite = True
        mock_settings.query_understand_model = ""
        mock_settings.intent_classifier = "choice"
        mock_settings.openjev_api_key = "test-key"
        mock_settings.openjev_base_url = "https://api.openjev.sh"
        mock_settings.openjev_model = "openjev"
        mock_settings.openjev_timeout_sec = 20
        out = query_understand(state, {})

    assert out["intent"] == "greeting"
    assert out["intent_confidence"] == 0.88
    hosted_call.assert_called_once()
    assert hosted_call.call_args.kwargs["api_key"] == "test-key"
    assert hosted_call.call_args.kwargs["state"]["query"]
    mock_llm.bind.assert_not_called()
    mock_llm.with_structured_output.assert_not_called()
