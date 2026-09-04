"""模型凭证加密工具（AES-256-GCM）。

- 密文格式: "enc:v1:<base64(nonce || ciphertext + tag)>"
- MASTER_KEY 未设置（保持默认值）时降级为可逆 base64（"b64:..."），仅限开发；
  生产必须设置 MASTER_KEY，否则读取加密凭证会显式报错。
- 支持 MASTER_KEY 轮换：encrypt/decrypt 可显式传入 key，见 scripts/reencrypt_models.py。
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config.settings import settings

_DEFAULT_MASTER_KEY = "knowsphere-dev-master-key-change-me"
_PREFIX_AES = "enc:v1:"
_PREFIX_B64 = "b64:"

def _master_key_bytes(key: Optional[str] = None) -> bytes:
    """任意长度密钥 -> 固定 32 字节（sha256）。"""
    raw = key if key is not None else settings.model_master_key
    return hashlib.sha256(raw.encode("utf-8")).digest()

def _is_degraded() -> bool:
    """未显式配置 MASTER_KEY 时为降级模式（可逆 base64）。"""
    return settings.model_master_key == _DEFAULT_MASTER_KEY

def encrypt_secret(plaintext: str, key: Optional[str] = None) -> str:
    """加密明文；空串原样返回。未配置 MASTER_KEY 时降级为可逆 base64。"""
    if not plaintext:
        return ""
    if key is None and _is_degraded:
        return _PREFIX_B64 + base64.urlsafe_b64encode(plaintext.encode("utf-8")).decode("ascii")
    k = _master_key_bytes(key)
    nonce = os.urandom(12)
    ct = AESGCM(k).encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return _PREFIX_AES + payload

def decrypt_secret(token: str, key: Optional[str] = None) -> str:
    """解密密文；空串原样返回。降级模式下密文是 base64 可直接解。"""
    if not token:
        return ""
    if token.startswith(_PREFIX_AES):
        if key is None and _is_degraded:
            raise ValueError(
                "MASTER_KEY 未配置（默认值），无法解密 AES 加密凭证；"
                "请在 .env 设置 MASTER_KEY 后运行 python -m scripts.reencrypt_models 重新加密"
            )
        k = _master_key_bytes(key)
        raw = base64.urlsafe_b64decode(token[len(_PREFIX_AES):])
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(k).decrypt(nonce, ct, None).decode("utf-8")
    if token.startswith(_PREFIX_B64):
        return base64.urlsafe_b64decode(token[len(_PREFIX_B64):]).decode("utf-8")
    # 历史明文（无前缀）：原样返回
    return token

def is_secret(token: str) -> bool:
    """判断是否为加密/降级存储的凭证。"""
    return token.startswith((_PREFIX_AES, _PREFIX_B64))
