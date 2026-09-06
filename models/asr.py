"""OpenAI 兼容 ASR：POST {base_url}/audio/transcriptions。"""

from __future__ import annotations

import base64
import logging
import mimetypes
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ASR_TIMEOUT_SEC = 300.0


@dataclass
class TranscriptionSegment:
    start: float = 0.0
    end: float = 0.0
    text: str = ""


@dataclass
class TranscriptionResult:
    text: str = ""
    segments: list[TranscriptionSegment] = field(default_factory=list)


def audio_transcriptions_url(base_url: str) -> str:
    """规范化 OpenAI 兼容转写地址。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("ASR 缺少 base_url")
    if base.endswith("/audio/transcriptions"):
        return base
    return f"{base}/audio/transcriptions"


def silent_wav_bytes(duration_ms: int = 200, sample_rate: int = 8000) -> bytes:
    """生成极短静音 WAV，供模型调试走真实转写接口。"""
    n = max(1, int(sample_rate * duration_ms / 1000))
    data = b"\x00\x00" * n
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def decode_audio_base64(raw: str | None) -> tuple[bytes, str]:
    """解析 data URI / 纯 base64，返回 (bytes, filename)。空则用静音 WAV。"""
    text = (raw or "").strip()
    if not text:
        return silent_wav_bytes(), "debug.wav"
    mime = "audio/wav"
    payload = text
    if text.startswith("data:"):
        header, _, payload = text.partition(",")
        if ";base64" not in header:
            raise ValueError("音频 data URI 须为 base64")
        mime_part = header[5:].split(";", 1)[0].strip()
        if mime_part:
            mime = mime_part
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法解码音频: {exc}") from exc
    if not data:
        raise ValueError("音频内容为空")
    ext = mimetypes.guess_extension(mime) or ".wav"
    if ext == ".mpga":
        ext = ".mp3"
    return data, f"debug{ext}"


def _parse_transcription_payload(payload: Any) -> TranscriptionResult:
    if isinstance(payload, str):
        return TranscriptionResult(text=payload.strip())
    if not isinstance(payload, dict):
        raise ValueError(f"ASR 响应格式无效: {type(payload).__name__}")
    text = str(payload.get("text") or "").strip()
    segments: list[TranscriptionSegment] = []
    raw_segs = payload.get("segments") or []
    if isinstance(raw_segs, list):
        for item in raw_segs:
            if not isinstance(item, dict):
                continue
            segments.append(
                TranscriptionSegment(
                    start=float(item.get("start") or 0),
                    end=float(item.get("end") or 0),
                    text=str(item.get("text") or "").strip(),
                )
            )
    return TranscriptionResult(text=text, segments=segments)


class ASRClient:
    """OpenAI 兼容语音转写客户端。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "",
        language: str = "",
        timeout: float = ASR_TIMEOUT_SEC,
    ) -> None:
        self.model = (model or "").strip()
        if not self.model:
            raise ValueError("ASR 模型名为空")
        self.api_key = api_key or ""
        self.base_url = base_url or ""
        self.language = (language or "").strip()
        self.timeout = timeout

    def transcribe(
        self,
        audio_bytes: bytes,
        file_name: str = "audio.mp3",
        language: str | None = None,
    ) -> TranscriptionResult:
        if not audio_bytes:
            raise ValueError("音频内容为空")
        safe_name = Path(file_name or "audio.mp3").name or "audio.mp3"
        if not Path(safe_name).suffix:
            safe_name = f"{safe_name}.mp3"
        mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        url = audio_transcriptions_url(self.base_url)
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data: dict[str, str] = {
            "model": self.model,
            "response_format": "verbose_json",
        }
        lang = (language if language is not None else self.language) or ""
        if lang:
            data["language"] = lang
        logger.info(
            "ASR transcribe model=%s url=%s file=%s size=%d",
            self.model,
            url,
            safe_name,
            len(audio_bytes),
        )
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                url,
                headers=headers,
                data=data,
                files={"file": (safe_name, audio_bytes, mime)},
            )
        if resp.status_code >= 400:
            detail = (resp.text or "")[:400]
            raise ValueError(f"ASR 转写失败 ({resp.status_code}): {detail}")
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"ASR 响应不是 JSON: {exc}") from exc
        result = _parse_transcription_payload(payload)
        logger.info("ASR transcribe done text_len=%d", len(result.text))
        return result
