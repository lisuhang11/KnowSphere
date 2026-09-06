"""音频解析器：调用 ASR 转写为 Markdown 正文。"""

from __future__ import annotations

from pathlib import Path

from ingestion.parser.base_parser import BaseParser, ParserError, ParseResult
from utils.audio import AUDIO_EXTENSIONS, KB_ASR_REQUIRED_DETAIL


class AudioParser(BaseParser):
    supported_file_types = [ext.lstrip(".") for ext in sorted(AUDIO_EXTENSIONS)]

    def parse(self, path: str) -> ParseResult:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise ParserError(f"读取音频失败: {exc}") from exc
        if not data:
            raise ParserError("音频内容为空")

        model_id = str(self.parse_options.get("asr_model_id") or "").strip()
        if not model_id:
            raise ParserError(KB_ASR_REQUIRED_DETAIL)

        from models import create_asr_model

        try:
            client = create_asr_model(model=model_id)
            result = client.transcribe(data, Path(path).name)
        except ParserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"语音识别失败: {exc}") from exc

        text = (result.text or "").strip()
        if not text:
            raise ParserError("语音识别结果为空")

        parsed = ParseResult()
        parsed.markdown = text
        return parsed


__all__ = ["AudioParser"]
