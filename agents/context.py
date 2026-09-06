"""运行时 Context：从 RunnableConfig.configurable 解析本轮开关与模型。

对齐官方模板的 Configuration / Context：节点与工具经 from_runnable_config 读取，
不直接翻 configurable 字典。checkpointer 仍使用 configurable.thread_id。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    if not config:
        return {}
    raw = config.get("configurable") or {}
    return raw if isinstance(raw, dict) else {}


def _parse_kb_ids(raw: Any) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for v in raw:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out.append(v)
            continue
        if isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                out.append(int(s))
    return out


def _parse_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        ident = str(item or "").strip()
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(ident)
    return out


def _optional_str(raw: Any) -> str:
    if raw is None:
        return ""
    ident = str(raw).strip()
    return ident


@dataclass(kw_only=True)
class Context:
    """本轮图运行配置（Studio / invoke 均可注入）。

    web_search_enabled / graph_enabled / skill_names 为 None 表示调用方未传，
    由 resolved_* 按产品默认值补全。
    """

    thread_id: str = ""
    kb_ids: list[int] = field(default_factory=list)
    chat_model_id: str = ""
    vlm_model_id: str = ""
    agent_id: str = ""
    owner: str = ""
    attachment_ids: list[str] = field(default_factory=list)
    pinned_skill_names: list[str] = field(default_factory=list)
    skill_names: list[str] | None = None
    web_search_enabled: bool | None = None
    graph_enabled: bool | None = None

    def resolved_web_search_enabled(self) -> bool:
        """管理员总开关关闭则一律 False；未传本轮开关时默认开启。"""
        from config.settings import settings

        if not settings.web_search_enabled:
            return False
        if self.web_search_enabled is None:
            return True
        return bool(self.web_search_enabled)

    def resolved_graph_enabled(self) -> bool:
        """无知识库则不可用；未传本轮开关时视为可用（兼容测试/评测）。"""
        if not self.kb_ids:
            return False
        if self.graph_enabled is None:
            return True
        return bool(self.graph_enabled)

    def chat_model_kwargs(self, base: dict[str, Any] | None = None) -> dict[str, Any]:
        kw = dict(base or {})
        if self.chat_model_id:
            kw["model"] = self.chat_model_id
        return kw

    @classmethod
    def from_runnable_config(cls, config: RunnableConfig | None = None) -> Context:
        raw = _configurable(config)
        skill_names: list[str] | None = None
        if "skill_names" in raw:
            skill_names = _parse_str_list(raw.get("skill_names"))
        return cls(
            thread_id=_optional_str(raw.get("thread_id")),
            kb_ids=_parse_kb_ids(raw.get("kb_ids")),
            chat_model_id=_optional_str(raw.get("chat_model_id")),
            vlm_model_id=_optional_str(raw.get("vlm_model_id")),
            agent_id=_optional_str(raw.get("agent_id")),
            owner=_optional_str(raw.get("owner")),
            attachment_ids=_parse_str_list(raw.get("attachment_ids")),
            pinned_skill_names=_parse_str_list(raw.get("pinned_skill_names")),
            skill_names=skill_names,
            web_search_enabled=(
                bool(raw["web_search_enabled"]) if "web_search_enabled" in raw else None
            ),
            graph_enabled=bool(raw["graph_enabled"]) if "graph_enabled" in raw else None,
        )


def context_from_config(config: RunnableConfig | None = None) -> Context:
    return Context.from_runnable_config(config)


__all__ = ["Context", "context_from_config"]
