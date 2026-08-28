"""doc_retrieval 本地 expansion 触发逻辑（mock 存储层）。"""

from __future__ import annotations

from unittest.mock import patch

from services.retrieval_service import RetrievalService


def test_doc_retrieval_runs_local_expansion_when_recall_low():
    query = "支付模块退款流程"
    sparse_rows = [
        {
            "document_id": "d1",
            "chunk_index": 0,
            "score": 0.5,
            "content": "a",
            "file_name": "a.md",
            "snippet": "a",
        }
    ]
    extra = [
        {
            "document_id": "d2",
            "chunk_index": 1,
            "score": 0.4,
            "content": "b",
            "file_name": "b.md",
            "snippet": "b",
        }
    ]
    svc = RetrievalService()

    with (
        patch("services.retrieval_service.settings") as mock_settings,
        patch.object(
            svc.store,
            "get_knowledge_base_configs",
            return_value={1: {"embedding_model_id": "m", "embedding_dim": 1024}},
        ),
        patch.object(svc, "_recall") as mock_recall,
        patch(
            "services.retrieval_service.expand_queries_local",
            return_value=["支付 退款"],
        ),
    ):
        mock_settings.retrieval_top_k = 6
        mock_settings.retrieval_candidate_k = 30
        mock_settings.rerank_enabled = False
        mock_settings.mmr_enabled = False
        mock_settings.multi_query_enabled = False
        mock_settings.query_expansion_enabled = True
        mock_settings.query_expansion_max_variants = 3
        mock_recall.side_effect = [sparse_rows, extra]

        out = svc.search(query, kb_ids=[1])

    assert mock_recall.call_count == 2
    assert len(out.sources) >= 1
