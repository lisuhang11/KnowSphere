"""按 __refId 取回外置工具结果全文。返回值不再二次外置。"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool

from utils.run_config import thread_id_from_config
from utils.tool_result_store import get_stored_result, parse_tool_payload


@tool
def get_stored_data(
    ref_id: str,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> dict[str, Any]:
    """取回先前被外置存储的完整工具结果。

    当上一条工具消息是 {"__stored": true, "__refId": "..."} 且 __summary
    不够用时调用。传入 __refId。不要编造 id，也不要把 summary 当成全文。
    """
    key = (ref_id or "").strip()
    if not key:
        return {"error": "ref_id 不能为空", "data": None}
    record = get_stored_result(
        key,
        thread_id=thread_id_from_config(config) or "",
    )
    if record is None:
        return {
            "error": "找不到该 ref_id 对应的数据，请重新调用原工具",
            "ref_id": key,
            "data": None,
        }
    return {
        "__refId": record.ref_id,
        "__toolType": record.tool_name,
        "__originalLength": record.original_length,
        "data": parse_tool_payload(record.payload),
    }
