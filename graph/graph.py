"""LangGraph 图导出：langgraph.json 引用的入口对象。

LangGraph Studio 通过 langgraph.json 的 graphs.agent 指向本模块的 graph
（StateGraph：agent → tools → collect_sources → agent）。
"""

from agents.agent import build_agent

graph = build_agent
