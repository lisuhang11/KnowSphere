"""MASTER_KEY 轮换工具：用旧 key 解密、新 key 重加密 models.parameters 中的凭证。

用法:
    MASTER_KEY=<旧密钥> MASTER_KEY_NEW=<新密钥> python -m scripts.reencrypt_models

- 旧 key 从 .env 的 MASTER_KEY（或当前环境）读取；
- 新 key 从环境变量 MASTER_KEY_NEW 读取；
- 解不开的密文跳过并告警，其余全部重写；
- 跑完后把 .env 中 MASTER_KEY 改为新密钥并重启 API。
"""

from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config.settings import settings
from utils.crypto import decrypt_secret, encrypt_secret
from utils.model_store import _SECRET_FIELDS

def main() -> int:
    new_key = os.getenv("MASTER_KEY_NEW", "")
    if not new_key:
        print("用法: MASTER_KEY_NEW=<新密钥> python -m scripts.reencrypt_models")
        return 1
    old_key = settings.model_master_key
    if old_key == new_key:
        print("MASTER_KEY_NEW 与当前 MASTER_KEY 相同，无需重加密")
        return 0

    rows = []
    with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT id, name, parameters FROM models WHERE parameters ? 'api_key'"
        ).fetchall()

    updated = skipped = 0
    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        for row in rows:
            raw = row["parameters"]
            params = dict(json.loads(raw)) if isinstance(raw, str) else dict(raw or {})
            changed = False
            for f in _SECRET_FIELDS:
                token = params.get(f)
                if not token:
                    continue
                try:
                    plain = decrypt_secret(str(token), key=old_key)
                    params[f] = encrypt_secret(plain, key=new_key)
                    changed = True
                except Exception as exc:  # noqa: BLE001
                    skipped += 1
                    print(f"跳过 {row['name']} ({row['id']}): {exc}")
            if changed:
                conn.execute(
                    "UPDATE models SET parameters = %s, updated_at = now() WHERE id = %s",
                    (Jsonb(params), row["id"]),
                )
                updated += 1

    print(f"重加密完成: 更新 {updated} 条，跳过 {skipped} 条")
    if updated:
        print("请将 .env 中 MASTER_KEY 更新为新密钥，然后重启 API 服务")
    return 0

if __name__ == "__main__":
    sys.exit(main)
