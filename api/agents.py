"""智能体 / 工具目录 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from skills.catalog import skills_to_public
from stores.agent_repository import AgentStore
from tools.catalog import tools_to_public

router = APIRouter(tags=["agents"])

_store = AgentStore()


class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    tool_names: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    max_iterations: int | None = None
    is_default: bool = False


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tool_names: list[str] | None = None
    skill_names: list[str] | None = None
    max_iterations: int | None = None
    is_default: bool | None = None
    status: str | None = None


def _http(exc: ValueError) -> HTTPException:
    msg = str(exc)
    code = 404 if "不存在" in msg else 400
    return HTTPException(status_code=code, detail=msg)


@router.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    """ListTools：代码目录中的全部内置工具。"""
    return tools_to_public()


@router.get("/skills")
def list_skills() -> list[dict[str, Any]]:
    """仓库内技能目录（name + description），供智能体勾选。"""
    return skills_to_public()


@router.get("/agents")
def list_agents() -> list[dict[str, Any]]:
    return _store.list_agents()


@router.post("/agents")
def create_agent(body: AgentCreateRequest) -> dict[str, Any]:
    try:
        return _store.create_agent(
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            tool_names=body.tool_names,
            skill_names=body.skill_names,
            max_iterations=body.max_iterations,
            is_default=body.is_default,
        )
    except ValueError as exc:
        raise _http(exc) from exc


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict[str, Any]:
    rec = _store.get_agent(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return rec


@router.put("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdateRequest) -> dict[str, Any]:
    try:
        return _store.update_agent(
            agent_id,
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            tool_names=body.tool_names,
            skill_names=body.skill_names,
            max_iterations=body.max_iterations,
            is_default=body.is_default,
            status=body.status,
        )
    except ValueError as exc:
        raise _http(exc) from exc


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str) -> dict[str, bool]:
    try:
        _store.delete_agent(agent_id)
    except ValueError as exc:
        raise _http(exc) from exc
    return {"ok": True}
