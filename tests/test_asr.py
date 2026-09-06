"""ASR 客户端、音频闸门与解析器测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.asr import (
    ASRClient,
    audio_transcriptions_url,
    decode_audio_base64,
    silent_wav_bytes,
    _parse_transcription_payload,
)
from utils.audio import (
    AGENT_AUDIO_DISABLED_DETAIL,
    KB_ASR_REQUIRED_DETAIL,
    NO_ASR_AUDIO_UPLOAD_DETAIL,
    ensure_kb_can_accept_audio,
    is_audio_filename,
    is_audio_upload,
)


def test_audio_transcriptions_url():
    assert audio_transcriptions_url("https://api.siliconflow.cn/v1") == (
        "https://api.siliconflow.cn/v1/audio/transcriptions"
    )
    assert audio_transcriptions_url("https://example.com/v1/audio/transcriptions") == (
        "https://example.com/v1/audio/transcriptions"
    )
    with pytest.raises(ValueError, match="base_url"):
        audio_transcriptions_url("  ")


def test_silent_wav_bytes_is_valid_riff():
    data = silent_wav_bytes()
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert len(data) > 44


def test_decode_audio_base64_empty_uses_silent_wav():
    data, name = decode_audio_base64(None)
    assert name.endswith(".wav")
    assert data[:4] == b"RIFF"


def test_parse_transcription_payload():
    parsed = _parse_transcription_payload(
        {"text": "你好", "segments": [{"start": 0, "end": 1.2, "text": "你好"}]}
    )
    assert parsed.text == "你好"
    assert len(parsed.segments) == 1
    assert parsed.segments[0].end == 1.2
    assert _parse_transcription_payload("plain").text == "plain"


def test_asr_client_transcribe_posts_multipart():
    client = ASRClient(
        model="FunAudioLLM/SenseVoiceSmall",
        api_key="sk-test",
        base_url="https://api.siliconflow.cn/v1",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "转写结果"}
    with patch("models.asr.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.post.return_value = mock_resp
        result = client.transcribe(b"audio-bytes", "meeting.mp3")
    assert result.text == "转写结果"
    kwargs = mock_cls.return_value.__enter__.return_value.post.call_args
    assert kwargs.args[0].endswith("/audio/transcriptions")
    assert kwargs.kwargs["data"]["model"] == "FunAudioLLM/SenseVoiceSmall"
    assert kwargs.kwargs["files"]["file"][0] == "meeting.mp3"


def test_asr_client_raises_on_http_error():
    client = ASRClient(model="whisper-1", base_url="https://api.openai.com/v1")
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "unauthorized"
    with patch("models.asr.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.post.return_value = mock_resp
        with pytest.raises(ValueError, match="401"):
            client.transcribe(b"x", "a.wav")


def test_is_audio_filename_and_upload():
    assert is_audio_filename("a.mp3")
    assert is_audio_filename("b.M4A")
    assert not is_audio_filename("c.pdf")
    assert is_audio_upload("notes.txt", "audio/mpeg")
    assert not is_audio_upload("notes.txt", "text/plain")


def test_ensure_kb_can_accept_audio():
    with pytest.raises(ValueError, match="ASR"):
        ensure_kb_can_accept_audio({"asr_enabled": False, "asr_model_id": ""})
    with pytest.raises(ValueError, match="ASR"):
        ensure_kb_can_accept_audio({"asr_enabled": True, "asr_model_id": ""})
    with patch("utils.model_store.ModelStore") as store_cls:
        store_cls.return_value.is_asr_model_id_valid.return_value = False
        with pytest.raises(ValueError, match="不存在"):
            ensure_kb_can_accept_audio({"asr_enabled": True, "asr_model_id": "model-asr"})
    with patch("utils.model_store.ModelStore") as store_cls:
        store_cls.return_value.is_asr_model_id_valid.return_value = True
        ensure_kb_can_accept_audio({"asr_enabled": True, "asr_model_id": "model-asr"})


def test_has_usable_asr(monkeypatch):
    from config.settings import settings
    from stores.model_repository import ModelStore

    monkeypatch.setattr(settings, "chat_asr_model_id", "")
    monkeypatch.setattr(ModelStore, "is_asr_model_id_valid", lambda self, mid: False)
    monkeypatch.setattr(ModelStore, "get_default_model", lambda self, t: None)
    monkeypatch.setattr(ModelStore, "list_models", lambda self, type_=None, source=None: [])
    assert ModelStore().has_usable_asr() is False

    monkeypatch.setattr(settings, "chat_asr_model_id", "model-asr")
    monkeypatch.setattr(ModelStore, "is_asr_model_id_valid", lambda self, mid: mid == "model-asr")
    assert ModelStore().has_usable_asr() is True
    assert ModelStore().resolve_asr_model_id() == "model-asr"


def test_audio_parser_requires_model(tmp_path: Path):
    from ingestion.parser.audio_parser import AudioParser
    from ingestion.parser.base_parser import ParserError

    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"id3fake")
    with pytest.raises(ParserError, match="ASR"):
        AudioParser({}).parse(str(audio))


def test_audio_parser_transcribes(tmp_path: Path):
    from ingestion.parser.audio_parser import AudioParser

    audio = tmp_path / "clip.wav"
    audio.write_bytes(silent_wav_bytes())
    fake = MagicMock()
    fake.transcribe.return_value = _parse_transcription_payload({"text": "会议纪要"})
    with patch("models.create_asr_model", return_value=fake):
        result = AudioParser({"asr_model_id": "model-asr"}).parse(str(audio))
    assert result.markdown == "会议纪要"
    fake.transcribe.assert_called_once()


def test_create_asr_model_requires_base_url(monkeypatch):
    from models.base import create_asr_model

    monkeypatch.setattr("models.base._resolve_from_db", lambda *a, **k: None)
    with pytest.raises(ValueError, match="base_url"):
        create_asr_model(model="whisper-1")


def test_parse_document_registers_audio():
    from ingestion.parser import ALLOWED_EXTENSIONS, get_parser_engine
    from ingestion.parser.audio_parser import AudioParser

    assert ".mp3" in ALLOWED_EXTENSIONS
    parser = get_parser_engine().get_parser(".mp3", {"asr_model_id": "model-asr"})
    assert isinstance(parser, AudioParser)


def test_gate_messages_are_stable():
    assert "ASR" in KB_ASR_REQUIRED_DETAIL
    assert "智能体" in AGENT_AUDIO_DISABLED_DETAIL
    assert "ASR" in NO_ASR_AUDIO_UPLOAD_DETAIL
