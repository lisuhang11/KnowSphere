"""工具统一注册表：新增工具后加入 get_tools。"""

from tools.retrieval.doc_retrieval import doc_retrieval

def get_tools() -> list:
    return [doc_retrieval]
