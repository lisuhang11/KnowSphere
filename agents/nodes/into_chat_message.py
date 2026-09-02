"""into_chat_message：把本轮检索片段格式化为生成上下文（不写入 messages）。"""

from __future__ import annotations

from schemas.query import needs_retrieval
from states import KnowSphereState


def format_context_block(sources: list[dict], note: str | None = None) -> str:
    """将 last_sources 编成模型可读的【知识库检索结果】块；序号从 1 起对齐 [[cN]]。"""
    lines = ["【知识库检索结果】"]
    if not sources:
        lines.append("未命中相关片段。请明确说明未在知识库中找到相关信息。")
        if note and note.strip():
            lines.append(note.strip())
        return "\n".join(lines)

    chunks: list[str] = []
    for i, item in enumerate(sources, 1):
        name = str(item.get("file_name") or "")
        idx = item.get("chunk_index", 0)
        snippet = str(item.get("snippet") or "").strip()
        header = f"[{i}] {name}#{idx}" if name else f"[{i}]"
        chunks.append(f"{header}\n{snippet}" if snippet else header)
    lines.append("\n\n".join(chunks))
    if note and note.strip():
        lines.append(note.strip())
    return "\n".join(lines)


def into_chat_message(state: KnowSphereState) -> dict:
    """仅在本轮实际检索时写入 context_block；闲聊/附件路径保持空串。"""
    sources = [s for s in (state.get("last_sources") or []) if isinstance(s, dict)]
    if needs_retrieval(state.get("intent"), bool(state.get("kb_selected"))) or sources:
        block = format_context_block(sources, state.get("retrieval_note"))
    else:
        block = ""
    return {"context_block": block}
