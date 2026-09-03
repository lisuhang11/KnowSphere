"""read_skill：按需加载 SKILL.md 或技能内文件（渐进披露 Level 2/3）。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from skills.catalog import get_skill
from skills.paths import MAX_READ_CHARS, list_skill_files, resolve_skill_file
from tools.events import emit_thinking, emit_tool_call, emit_tool_result
from utils.agent_runtime import resolve_agent_skill_names


def _allowed(config: RunnableConfig | None) -> frozenset[str]:
    return frozenset(resolve_agent_skill_names(config))


def _clip(text: str) -> str:
    raw = text or ""
    if len(raw) <= MAX_READ_CHARS:
        return raw
    return raw[:MAX_READ_CHARS] + "\n\n…(已截断)"


@tool
def read_skill(
    skill_name: str,
    file_path: str = "",
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> str:
    """读取已绑定技能的说明书或技能包内文件。匹配到技能后先调用本工具再执行脚本。

    不传 file_path 时返回 SKILL.md 正文，并列出技能内可读取的相对路径。
    file_path 为技能目录内相对路径，例如 scripts/extract_text.py。
    """
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    name = (skill_name or "").strip()
    rel = (file_path or "").strip()
    emit_tool_call("read_skill", f"正在读取技能：「{name}」…", writer)
    emit_thinking(f"【读取技能】{name}" + (f" / {rel}" if rel else ""), writer)

    allowed = _allowed(config)
    if not allowed:
        msg = "当前智能体未启用技能。"
        emit_tool_result("read_skill", msg, success=False, writer=writer)
        return msg
    if name not in allowed:
        msg = f"技能未绑定到当前智能体: {name}。可用: {', '.join(sorted(allowed))}"
        emit_tool_result("read_skill", msg, success=False, writer=writer)
        return msg

    rec = get_skill(name)
    if rec is None:
        msg = f"技能不存在: {name}"
        emit_tool_result("read_skill", msg, success=False, writer=writer)
        return msg

    if rel:
        path = resolve_skill_file(name, rel)
        if path is None:
            msg = f"无法读取 {rel}：路径无效、越界，或不在技能目录内。"
            emit_tool_result("read_skill", msg, success=False, writer=writer)
            return msg
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            msg = f"{rel} 不是 UTF-8 文本，无法展示。"
            emit_tool_result("read_skill", msg, success=False, writer=writer)
            return msg
        except OSError as exc:
            msg = f"读取 {rel} 失败: {exc}"
            emit_tool_result("read_skill", msg, success=False, writer=writer)
            return msg
        body = _clip(text)
        emit_tool_result("read_skill", f"已读取 {name}/{rel}", writer=writer)
        return f"# {name}/{rel}\n\n{body}"

    files = list_skill_files(name)
    listing = "\n".join(f"- {item}" for item in files) if files else "- （无其它文件）"
    body = _clip(rec.instructions or "（SKILL.md 正文为空）")
    emit_tool_result("read_skill", f"已加载技能 {name}", writer=writer)
    return f"# {name}\n\n{body}\n\n## Files\n{listing}"
