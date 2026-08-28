"""多跳在首轮命中不足时才触发。"""

from __future__ import annotations

from unittest.mock import patch

from services.retrieval_service import RetrievalService


def _row(doc: str, idx: int, score: float = 0.5) -> dict:
    return {
        "document_id": doc,
        "chunk_index": idx,
        "score": score,
        "content": f"c{idx}",
        "file_name": f"{doc}.md",
        "snippet": f"s{idx}",
    }


def test_multi_query_triggers_only_when_first_recall_sparse():
    query = "李稣航是谁"
    sparse = [_row("d1", i) for i in range(2)]  # < threshold (top_k//2 == 3)
    mq_hits = [_row("d2", i, 0.4) for i in range(2)]
    thoughts: list[str] = []
    svc = RetrievalService()

    with (
        patch("services.retrieval_service.settings") as mock_settings,
        patch.object(
            svc.store,
            "get_knowledge_base_configs",
            return_value={1: {"embedding_model_id": "m", "embedding_dim": 1024}},
        ),
        patch.object(svc, "_generate_sub_queries", return_value=["子问A", "子问B"]) as mock_mq,
        patch.object(svc, "_recall") as mock_recall,
        patch.object(svc, "_rerank_rows", side_effect=lambda q, rows, n: rows[:n]),
    ):
        mock_settings.retrieval_top_k = 6
        mock_settings.retrieval_candidate_k = 30
        mock_settings.rerank_enabled = False
        mock_settings.mmr_enabled = False
        mock_settings.multi_query_enabled = True
        mock_settings.multi_query_count = 2
        mock_settings.query_expansion_enabled = False
        mock_recall.side_effect = [sparse, mq_hits]

        out = svc.search(query, kb_ids=[1], on_thinking=thoughts.append)

    assert mock_mq.called
    assert mock_recall.call_count == 2
    assert mock_recall.call_args_list[0].args[0] == [query]
    assert mock_recall.call_args_list[1].args[0] == ["子问A", "子问B"]
    joined = "\n".join(thoughts)
    assert "因此触发 LLM 多跳" in joined
    assert "首轮单路仅 2 条" in joined
    assert len(out.sources) >= 1


def test_multi_query_skipped_when_first_recall_enough():
    query = "李稣航是谁"
    rich = [_row("d1", i) for i in range(20)]
    svc = RetrievalService()

    with (
        patch("services.retrieval_service.settings") as mock_settings,
        patch.object(
            svc.store,
            "get_knowledge_base_configs",
            return_value={1: {"embedding_model_id": "m", "embedding_dim": 1024}},
        ),
        patch.object(svc, "_generate_sub_queries") as mock_mq,
        patch.object(svc, "_recall", return_value=rich) as mock_recall,
    ):
        mock_settings.retrieval_top_k = 6
        mock_settings.retrieval_candidate_k = 30
        mock_settings.rerank_enabled = False
        mock_settings.mmr_enabled = False
        mock_settings.multi_query_enabled = True
        mock_settings.multi_query_count = 2
        mock_settings.query_expansion_enabled = False

        svc.search(query, kb_ids=[1])

    mock_mq.assert_not_called()
    assert mock_recall.call_count == 1
