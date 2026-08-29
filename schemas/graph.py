"""知识图谱数据结构（对齐 WeKnora GraphNode / GraphRelation / NameSpace）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GraphNode:
    name: str
    chunks: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)


@dataclass
class GraphRelation:
    node1: str
    node2: str
    type: str


@dataclass
class GraphData:
    text: str = ""
    nodes: list[GraphNode] = field(default_factory=list)
    relations: list[GraphRelation] = field(default_factory=list)


@dataclass(frozen=True)
class NameSpace:
    """文档级子图命名空间：按 knowledge_base + document 隔离，KB 标签用于跨文档检索。"""

    knowledge_base: str
    document: str = ""

    def labels(self) -> list[str]:
        out: list[str] = []
        if self.knowledge_base:
            out.append(self.knowledge_base)
        if self.document:
            out.append(self.document)
        return out
