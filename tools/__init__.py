"""工具统一注册表：新增工具后加入 get_tools。"""

from tools.retrieval.doc_retrieval import doc_retrieval
from tools.retrieval.query_knowledge_graph import query_knowledge_graph


def get_tools() -> list:
    return [doc_retrieval, query_knowledge_graph]
