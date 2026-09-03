"""execute_skill_script：在一次性 Docker 容器中运行技能目录内脚本。"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from sandbox.docker_runner import run_skill_script
from skills.paths import resolve_skill_file
from tools.events import emit_file_artifact, emit_thinking, emit_tool_call, emit_tool_result
from utils.agent_runtime import resolve_agent_skill_names
from utils.object_store import guess_content_type
from utils.run_config import attachment_ids_from_config, thread_id_from_config

logger = logging.getLogger(__name__)


def _config_from_runtime(runtime: ToolRuntime | None) -> RunnableConfig | None:
    if runtime is None:
        return None
    cfg = getattr(runtime, "config", None)
    if isinstance(cfg, dict):
        return cfg
    return None


def _session_id(config: RunnableConfig | None, runtime: ToolRuntime | None) -> str | None:
    return thread_id_from_config(config) or thread_id_from_config(_config_from_runtime(runtime))


def _save_output_file(session_id: str, file_name: str, data: bytes) -> dict[str, Any] | None:
    from pathlib import Path

    from ingestion.parser import ALLOWED_EXTENSIONS
    from utils.temporary_attachments import TemporaryAttachmentStore

    if not data:
        return None
    original = file_name
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        stem = Path(original).stem or original
        file_name = f"{stem}.txt"
    store = TemporaryAttachmentStore()
    row = store.create(
        session_id=session_id,
        file_name=file_name,
        mime_type=guess_content_type(file_name),
        file_size=len(data),
        data=data,
    )
    try:
        store.mark_ready(row["id"], content=f"[skill output] {file_name}")
    except Exception:
        logger.debug("标记技能产出为 ready 失败", exc_info=True)
    return row


@tool
def execute_skill_script(
    skill_name: str,
    script_path: str,
    script_args: list[str] | None = None,
    stdin_text: str = "",
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> str:
    """在隔离沙箱中运行技能包内的脚本。script_path 必须是技能目录内相对路径。

    script_args 为命令行参数列表，用户附件请使用 /workspace/input/<文件名>。
    stdin_text 会写入脚本的标准输入。生成文件请写到 /workspace/output。
    """
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    name = (skill_name or "").strip()
    rel = (script_path or "").strip()
    emit_tool_call("execute_skill_script", f"正在沙箱执行：「{name}/{rel}」…", writer)
    emit_thinking(f"【沙箱执行】{name} {rel}", writer)

    allowed = frozenset(resolve_agent_skill_names(config))
    if not allowed:
        msg = "当前智能体未启用技能，无法执行脚本。"
        emit_tool_result("execute_skill_script", msg, success=False, writer=writer)
        return json.dumps({"ok": False, "message": msg}, ensure_ascii=False)
    if name not in allowed:
        msg = f"技能未绑定到当前智能体: {name}"
        emit_tool_result("execute_skill_script", msg, success=False, writer=writer)
        return json.dumps({"ok": False, "message": msg}, ensure_ascii=False)
    if resolve_skill_file(name, rel) is None:
        msg = f"脚本路径无效或不在技能目录内: {rel}"
        emit_tool_result("execute_skill_script", msg, success=False, writer=writer)
        return json.dumps({"ok": False, "message": msg}, ensure_ascii=False)

    session_id = _session_id(config, runtime)
    result = run_skill_script(
        skill_name=name,
        script_path=rel,
        args=script_args or [],
        stdin_text=stdin_text or "",
        session_id=session_id,
        attachment_ids=attachment_ids_from_config(config) or attachment_ids_from_config(
            _config_from_runtime(runtime)
        ),
    )
    if result.error:
        emit_tool_result("execute_skill_script", result.error, success=False, writer=writer)
        return json.dumps({"ok": False, "message": result.error}, ensure_ascii=False)

    artifacts: list[dict[str, Any]] = []
    skipped: list[str] = []
    if session_id:
        for item in result.output_files:
            try:
                row = _save_output_file(session_id, item.name, item.data)
            except Exception as exc:
                skipped.append(f"{item.name}: {exc}")
                continue
            if not row:
                continue
            artifact = {
                "id": row["id"],
                "file_name": row["file_name"],
                "file_type": row.get("file_type") or "",
                "file_size": int(row.get("file_size") or 0),
                "mime_type": row.get("mime_type") or "",
            }
            artifacts.append(artifact)
            emit_file_artifact(
                attachment_id=artifact["id"],
                file_name=artifact["file_name"],
                file_type=artifact["file_type"],
                file_size=artifact["file_size"],
                mime_type=artifact["mime_type"],
                writer=writer,
            )
    elif result.output_files:
        skipped.append("缺少会话，未能保存产出文件")

    ok = result.exit_code == 0
    summary_parts = [f"退出码 {result.exit_code}"]
    if artifacts:
        summary_parts.append("产出 " + "、".join(a["file_name"] for a in artifacts))
    if skipped:
        summary_parts.append("部分文件未保存: " + "; ".join(skipped[:3]))
    summary = "；".join(summary_parts)
    emit_tool_result("execute_skill_script", summary, success=ok, writer=writer)
    payload = {
        "ok": ok,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "artifacts": artifacts,
        "message": summary,
    }
    if skipped:
        payload["skipped"] = skipped
    return json.dumps(payload, ensure_ascii=False)
