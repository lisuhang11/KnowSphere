"""LangGraph 图导出：langgraph.json 引用的入口对象。

graphs.agent → 智能推理 ReAct（prepare_context → query_understand → agent ↔ tools）。
"""

from agents.agent import build_agent

graph = build_agent
