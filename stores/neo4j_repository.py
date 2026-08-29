"""Neo4j 知识图谱仓储：AddGraph / DelGraph / SearchNode。

设计对齐 WeKnora：
- 节点标签 ENTITY{kb_id}[:ENTITY{document_id}]，连字符替换为下划线
- 合并键 (name, kg=document_id)；同文档同名实体合并 chunks
- 检索：name CONTAINS 模糊匹配 + 一跳邻居
"""

from __future__ import annotations

import logging
import re
from typing import Any

from config.settings import settings
from schemas.graph import GraphData, GraphNode, GraphRelation, NameSpace

logger = logging.getLogger(__name__)

_LABEL_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _safe_label_part(value: str) -> str:
    return _LABEL_SAFE.sub("_", value.replace("-", "_"))


class Neo4jGraphRepository:
    """RetrieveGraphRepository 的 Neo4j 实现；driver 为 None 时全部 no-op。"""

    def __init__(self, driver: Any | None = None, node_prefix: str = "ENTITY") -> None:
        self.driver = driver
        self.node_prefix = node_prefix

    @classmethod
    def from_settings(cls) -> "Neo4jGraphRepository":
        if not settings.neo4j_enable:
            return cls(driver=None)
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
            )
            driver.verify_connectivity()
            logger.info("Neo4j connected: %s", settings.neo4j_uri)
            return cls(driver=driver)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j unavailable (%s); graph ops will no-op", exc)
            return cls(driver=None)

    @property
    def enabled(self) -> bool:
        return self.driver is not None

    def labels(self, namespace: NameSpace) -> list[str]:
        return [f"{self.node_prefix}{_safe_label_part(x)}" for x in namespace.labels()]

    def label_expr(self, namespace: NameSpace) -> str:
        return ":".join(self.labels(namespace))

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def add_graph(self, namespace: NameSpace, graphs: list[GraphData]) -> None:
        if self.driver is None:
            logger.debug("Neo4j disabled, skip AddGraph")
            return
        for graph in graphs:
            self._add_one(namespace, graph)

    def _add_one(self, namespace: NameSpace, graph: GraphData) -> None:
        assert self.driver is not None
        labels = self.labels(namespace)
        node_rows = [
            {
                "name": n.name,
                "knowledge_id": namespace.document,
                "attributes": n.attributes or [],
                "chunks": [str(c) for c in (n.chunks or [])],
                "labels": labels,
            }
            for n in graph.nodes
            if n.name
        ]
        rel_rows = [
            {
                "source": r.node1,
                "target": r.node2,
                "knowledge_id": namespace.document,
                "type": _safe_rel_type(r.type),
                "labels": labels,
            }
            for r in graph.relations
            if r.node1 and r.node2 and r.type
        ]

        with self.driver.session() as session:
            if node_rows:
                session.execute_write(_write_nodes, node_rows)
            if rel_rows:
                session.execute_write(_write_rels, rel_rows)

    def del_graph(self, namespaces: list[NameSpace]) -> None:
        if self.driver is None:
            logger.debug("Neo4j disabled, skip DelGraph")
            return
        with self.driver.session() as session:
            for ns in namespaces:
                label = self.label_expr(ns)
                if not label:
                    continue
                session.execute_write(_delete_ns, label, ns.document)

    def search_node(self, namespace: NameSpace, nodes: list[str]) -> GraphData:
        if self.driver is None or not nodes:
            return GraphData()
        label = self.label_expr(namespace)
        if not label:
            return GraphData()
        with self.driver.session() as session:
            return session.execute_read(_search_nodes, label, nodes)


def _safe_rel_type(value: str) -> str:
    cleaned = _LABEL_SAFE.sub("_", value.strip().replace("-", "_").replace(" ", "_"))
    return cleaned or "RELATED"


def _write_nodes(tx: Any, rows: list[dict]) -> None:
    # 使用 APOC 幂等合并；若 APOC 不可用则降级为纯 Cypher MERGE
    query_apoc = """
    UNWIND $data AS row
    CALL apoc.merge.node(row.labels, {name: row.name, kg: row.knowledge_id},
                         {attributes: row.attributes}, {}) YIELD node
    SET node.chunks = apoc.coll.union(coalesce(node.chunks, []), row.chunks)
    RETURN count(node) AS c
    """
    try:
        tx.run(query_apoc, data=rows)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("APOC merge unavailable (%s), fallback to Cypher MERGE", exc)

    for row in rows:
        label = ":".join(row["labels"])
        q = f"""
        MERGE (n:{label} {{name: $name, kg: $kg}})
        ON CREATE SET n.attributes = $attributes, n.chunks = $chunks
        ON MATCH SET
          n.attributes = CASE WHEN n.attributes IS NULL THEN $attributes ELSE n.attributes END,
          n.chunks = reduce(
            acc = [],
            x IN coalesce(n.chunks, []) + $chunks |
              CASE WHEN x IN acc THEN acc ELSE acc + x END
          )
        """
        tx.run(
            q,
            name=row["name"],
            kg=row["knowledge_id"],
            attributes=row["attributes"],
            chunks=row["chunks"],
        )


def _write_rels(tx: Any, rows: list[dict]) -> None:
    query_apoc = """
    UNWIND $data AS row
    CALL apoc.merge.node(row.labels, {name: row.source, kg: row.knowledge_id}, {}, {})
      YIELD node AS source
    CALL apoc.merge.node(row.labels, {name: row.target, kg: row.knowledge_id}, {}, {})
      YIELD node AS target
    CALL apoc.merge.relationship(source, row.type, {}, {}, target) YIELD rel
    RETURN count(rel) AS c
    """
    try:
        tx.run(query_apoc, data=rows)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("APOC rel merge unavailable (%s), fallback to Cypher MERGE", exc)

    for row in rows:
        label = ":".join(row["labels"])
        rel = row["type"]
        q = f"""
        MERGE (a:{label} {{name: $source, kg: $kg}})
        MERGE (b:{label} {{name: $target, kg: $kg}})
        MERGE (a)-[r:{rel}]->(b)
        """
        tx.run(q, source=row["source"], target=row["target"], kg=row["knowledge_id"])


def _delete_ns(tx: Any, label_expr: str, knowledge_id: str) -> None:
    if knowledge_id:
        tx.run(
            f"MATCH (n:{label_expr} {{kg: $kg}})-[r]-() DELETE r",
            kg=knowledge_id,
        )
        tx.run(f"MATCH (n:{label_expr} {{kg: $kg}}) DELETE n", kg=knowledge_id)
    else:
        # 仅 KB 标签：删该库下全部节点
        tx.run(f"MATCH (n:{label_expr})-[r]-() DELETE r")
        tx.run(f"MATCH (n:{label_expr}) DELETE n")


def _search_nodes(tx: Any, label_expr: str, nodes: list[str]) -> GraphData:
    result = tx.run(
        f"""
        MATCH (n:{label_expr})-[r]-(m:{label_expr})
        WHERE ANY(nodeText IN $nodes WHERE n.name CONTAINS nodeText)
        RETURN n, r, m
        """,
        nodes=nodes,
    )
    graph = GraphData()
    seen: set[str] = set()
    for record in result:
        for key in ("n", "m"):
            node = record[key]
            name = node.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            chunks = node.get("chunks") or []
            attrs = node.get("attributes") or []
            graph.nodes.append(
                GraphNode(
                    name=name,
                    chunks=[str(c) for c in chunks],
                    attributes=[str(a) for a in attrs],
                )
            )
        rel = record["r"]
        graph.relations.append(
            GraphRelation(
                node1=record["n"].get("name") or "",
                node2=record["m"].get("name") or "",
                type=rel.type,
            )
        )
    return graph


_repo: Neo4jGraphRepository | None = None


def get_graph_repository() -> Neo4jGraphRepository:
    global _repo
    if _repo is None:
        _repo = Neo4jGraphRepository.from_settings()
    return _repo
