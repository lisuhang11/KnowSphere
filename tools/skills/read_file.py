"""read_file：在进程内按虚拟路径读取已绑定技能的文件。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from skills.paths import list_skill_files, parse_skill_virtual_path, resolve_skill_file, skill_virtual_path
from tools.events import emit_thinking, emit_tool_call, emit_tool_result
from utils.agent_runtime import resolve_agent_skill_names

DEFAULT_READ_LIMIT = 100
MAX_READ_LIMIT = 2000


def _window(text: str, offset: int, limit: int) -> str:
    lines = text.splitlines()
    total = len(lines)
    start = max(0, offset)
    if start >= total:
        return f"共 {total} 行，offset={start} 已超出文件末尾。"
    chunk = lines[start : start + limit]
    end = start + len(chunk)
    numbered = [f"{index + 1:6}|{line}" for index, line in enumerate(chunk, start=start)]
    body = "\n".join(numbered)
    if end < total:
        body += (
            f"\n\n（共 {total} 行，已显示 {start + 1}-{end}。"
            f"继续请 read_file，offset={end}。）"
        )
    elif total:
        body += f"\n\n（共 {total} 行，已显示 {start + 1}-{end}。）"
    return body or "（文件为空）"


@tool
def read_file(
    file_path: str,
    offset: int = 0,
    limit: int = DEFAULT_READ_LIMIT,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> str:
    """读取已绑定技能中的文件。路径必须是 /skills/<name>/...，例如 /skills/pdf-extract/SKILL.md。

    offset 是从 0 开始的行号，limit 是最多返回的行数（默认 100）。
    只读当前智能体已绑定的技能，不能读宿主机其它路径。
    """
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    raw_path = (file_path or "").strip()
    emit_tool_call("read_file", f"正在读取：{raw_path or '（空路径）'}", writer)
    emit_thinking(f"【读取文件】{raw_path}", writer)

    allowed = frozenset(resolve_agent_skill_names(config))
    if not allowed:
        msg = "当前智能体未启用技能。"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg

    parsed = parse_skill_virtual_path(raw_path)
    if parsed is None:
        listing = "、".join(skill_virtual_path(name) for name in sorted(allowed))
        msg = f"路径无效: {raw_path or '（空）'}。只能读取已绑定技能，例如 {listing}。"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg

    name, rel = parsed
    if name not in allowed:
        listing = "、".join(skill_virtual_path(item) for item in sorted(allowed))
        msg = f"技能未绑定到当前智能体: {name}。可读取: {listing}"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg

    path = resolve_skill_file(name, rel)
    if path is None:
        available = "、".join(skill_virtual_path(name, item) for item in list_skill_files(name)) or "（无）"
        msg = f"无法读取 {skill_virtual_path(name, rel)}：不是该技能内的文件。可读取：{available}。"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg

    try:
        page_offset = int(offset)
    except (TypeError, ValueError):
        page_offset = 0
    try:
        page_limit = int(limit)
    except (TypeError, ValueError):
        page_limit = DEFAULT_READ_LIMIT
    if page_limit <= 0:
        msg = "limit 必须大于 0。"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg
    page_limit = min(page_limit, MAX_READ_LIMIT)

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        msg = f"{skill_virtual_path(name, rel)} 不是 UTF-8 文本，无法展示。"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg
    except OSError as exc:
        msg = f"读取 {skill_virtual_path(name, rel)} 失败: {exc}"
        emit_tool_result("read_file", msg, success=False, writer=writer)
        return msg

    shown = skill_virtual_path(name, rel)
    emit_tool_result("read_file", f"已读取 {shown}", writer=writer)
    return f"# {shown}\n\n{_window(text, page_offset, page_limit)}"
