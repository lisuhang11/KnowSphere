"""文档生命周期路由：创建/列表挂在知识库下，旧路径保留别名。"""

from __future__ import annotations

from api.documents import documents_router


def _route_map() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for route in documents_router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        out.setdefault(path, set()).update(methods)
    return out


def test_document_create_and_list_live_under_knowledge_base():
    routes = _route_map()
    assert "POST" in routes["/knowledge-bases/{kb_id}/documents"]
    assert "GET" in routes["/knowledge-bases/{kb_id}/documents"]
    assert "POST" in routes["/upload"]
    assert "GET" in routes["/documents"]
