"""一次性 Docker 沙箱：运行技能目录内脚本，不在 API 进程执行用户/模型代码。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import settings
from skills.paths import resolve_skill_dir, resolve_skill_file

logger = logging.getLogger(__name__)

_DOCKER_CHECKED: bool | None = None

CONTAINER_SKILL_ROOT = "/opt/knowsphere/skills"
CONTAINER_INPUT = "/workspace/input"
CONTAINER_OUTPUT = "/workspace/output"
MAX_STDIO_CHARS = 80_000


@dataclass
class SkillOutputFile:
    name: str
    data: bytes


@dataclass
class SkillRunResult:
    exit_code: int = 1
    stdout: str = ""
    stderr: str = ""
    output_files: list[SkillOutputFile] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0


def docker_available() -> bool:
    global _DOCKER_CHECKED
    if _DOCKER_CHECKED is not None:
        return _DOCKER_CHECKED
    if not shutil.which("docker"):
        _DOCKER_CHECKED = False
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=8,
            check=False,
        )
        _DOCKER_CHECKED = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _DOCKER_CHECKED = False
    return _DOCKER_CHECKED


def reset_docker_available_cache() -> None:
    global _DOCKER_CHECKED
    _DOCKER_CHECKED = None


def _clip(text: str) -> str:
    raw = text or ""
    if len(raw) <= MAX_STDIO_CHARS:
        return raw
    return raw[:MAX_STDIO_CHARS] + "\n…(已截断)"


def _image_present(image: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _safe_filename(name: str, used: set[str]) -> str:
    base = Path(name or "file").name or "file"
    candidate = base
    i = 1
    while candidate in used:
        stem = Path(base).stem
        suffix = Path(base).suffix
        candidate = f"{stem}_{i}{suffix}"
        i += 1
    used.add(candidate)
    return candidate


def _stage_attachments(input_dir: Path, session_id: str | None, attachment_ids: Sequence[str]) -> list[str]:
    if not session_id or not attachment_ids:
        return []
    names: list[str] = []
    used: set[str] = set()
    try:
        from utils.object_store import require_object_store
        from utils.temporary_attachments import TemporaryAttachmentStore
    except Exception as exc:
        logger.warning("加载附件存储失败: %s", exc)
        return []
    store = TemporaryAttachmentStore()
    try:
        obj = require_object_store()
    except Exception as exc:
        logger.warning("对象存储不可用，跳过附件拷贝: %s", exc)
        return []
    for aid in attachment_ids:
        ident = str(aid or "").strip()
        if not ident:
            continue
        row = store.get(ident, session_id)
        if not row:
            continue
        key = (row.get("storage_key") or "").strip()
        if not key:
            continue
        try:
            data, _ = obj.get_bytes(key)
        except Exception as exc:
            logger.warning("读取附件失败 %s: %s", ident, exc)
            continue
        fname = _safe_filename(str(row.get("file_name") or "upload"), used)
        dest = input_dir / fname
        dest.write_bytes(data)
        dest.chmod(0o444)
        names.append(fname)
    return names


def _collect_outputs(output_dir: Path) -> list[SkillOutputFile]:
    if not output_dir.is_dir():
        return []
    files: list[SkillOutputFile] = []
    used: set[str] = set()
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data:
            continue
        name = _safe_filename(path.name, used)
        files.append(SkillOutputFile(name=name, data=data))
    return files


def run_skill_script(
    *,
    skill_name: str,
    script_path: str,
    args: Sequence[str] | None = None,
    stdin_text: str = "",
    session_id: str | None = None,
    attachment_ids: Sequence[str] | None = None,
) -> SkillRunResult:
    """在一次性容器中执行技能脚本。Docker 不可用时返回错误，绝不在宿主机跑。"""
    if not docker_available():
        return SkillRunResult(
            error="技能沙箱不可用：未检测到 Docker。请安装并启动 Docker 后重试，脚本不会在 API 进程中执行。"
        )
    skill_dir = resolve_skill_dir(skill_name)
    if skill_dir is None:
        return SkillRunResult(error=f"技能不存在: {skill_name}")
    script = resolve_skill_file(skill_name, script_path)
    if script is None:
        return SkillRunResult(error=f"脚本路径无效或不存在: {script_path}")
    if script.suffix.lower() != ".py":
        return SkillRunResult(error="本期仅支持运行技能目录内的 .py 脚本。")

    image = (settings.skill_sandbox_image or "python:3.12-slim").strip()
    if not _image_present(image):
        return SkillRunResult(
            error=(
                f"沙箱镜像不存在: {image}。请先执行 docker pull {image}，"
                "或构建 sandbox/Dockerfile 后设置 SKILL_SANDBOX_IMAGE。"
            )
        )

    rel = script.relative_to(skill_dir).as_posix()
    container_script = f"{CONTAINER_SKILL_ROOT}/{skill_name}/{rel}"
    argv = [str(a) for a in (args or []) if str(a) is not None]
    timeout = max(5.0, float(settings.skill_sandbox_timeout_sec))
    memory = (settings.skill_sandbox_memory or "512m").strip() or "512m"
    name = f"ks-skill-{uuid.uuid4().hex[:12]}"
    work = Path(tempfile.mkdtemp(prefix="ks-skill-"))
    input_dir = work / "input"
    output_dir = work / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    os.chmod(work, 0o777)
    os.chmod(input_dir, 0o777)
    os.chmod(output_dir, 0o777)
    staged = _stage_attachments(input_dir, session_id, list(attachment_ids or []))

    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network=none",
        f"--memory={memory}",
        "--pids-limit=128",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-e",
        f"KNOWSPHERE_SKILL_DIR={CONTAINER_SKILL_ROOT}/{skill_name}",
        "-e",
        f"KNOWSPHERE_SKILL_OUTPUT_DIR={CONTAINER_OUTPUT}",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-v",
        f"{skill_dir}:{CONTAINER_SKILL_ROOT}/{skill_name}:ro",
        "-v",
        f"{input_dir}:{CONTAINER_INPUT}:ro",
        "-v",
        f"{output_dir}:{CONTAINER_OUTPUT}",
        "-w",
        "/workspace",
        image,
        "python",
        "-u",
        container_script,
        *argv,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=(stdin_text or "").encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        result = SkillRunResult(
            exit_code=int(proc.returncode),
            stdout=_clip(proc.stdout.decode("utf-8", errors="replace")),
            stderr=_clip(proc.stderr.decode("utf-8", errors="replace")),
            output_files=_collect_outputs(output_dir),
        )
        if staged and "input" not in result.stdout.lower():
            result.stdout = (
                (result.stdout + "\n" if result.stdout else "")
                + f"（已挂载附件: {', '.join(staged)}）"
            ).strip()
        return result
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        return SkillRunResult(error=f"技能脚本超时（{int(timeout)}s），容器已终止。")
    except OSError as exc:
        return SkillRunResult(error=f"无法启动 Docker: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
