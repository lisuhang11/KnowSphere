"""Pydantic schema：工具入参/输出、引用来源等。"""

from __future__ import annotations

from pydantic import BaseModel, Field

class Source(BaseModel):
    """单个检索命中的来源片段。"""

    document_id: str = Field(description="文档 ID")
    file_name: str = Field(description="文件名")
    chunk_index: int = Field(description="片段序号")
    score: float = Field(description="混合检索得分")
    snippet: str = Field(description="片段摘要（前 300 字）")
    parent_resolved: bool = Field(default=False, description="是否已从子块扩展为父块上下文")
    sub_chunk_index: int | None = Field(default=None, description="原始命中的子块序号")

class RetrievalResult(BaseModel):
    """doc_retrieval 工具输出。"""

    query: str
    sources: list[Source]
    note: str | None = Field(
        default=None,
        description="检索说明：未选择知识库或所选库不可用时的提示，供 LLM 判断是否基于自身知识回答",
    )
