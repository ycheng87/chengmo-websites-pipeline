"""运行配置：只从环境变量读取，禁止任何硬编码密钥。

- META_BASE_URL  OpenAI 兼容接口地址（默认 https://api.meta.ai/v1）
- META_MODEL     模型名（默认 muse-spark-1.3）
- MODEL_API_KEY  API key（dry-run 不需要）
- PIPELINE_HTTP_TIMEOUT      网络超时秒数（默认 90：site/email 单次生成 20~30s，20s 会误杀）
- PIPELINE_MAX_TOOL_ROUNDS   每步最大工具轮数（默认 8）
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


@dataclass(frozen=True)
class Config:
    base_url: str
    model: str
    api_key: str | None
    http_timeout_s: float
    max_tool_rounds: int
    dry_run: bool

    def summary(self) -> str:
        key_state = "set" if self.api_key else "unset (dry-run 不需要)"
        return (
            f"mode={'dry-run' if self.dry_run else 'live'}  "
            f"model={self.model}  base_url={self.base_url}  api_key={key_state}"
        )


def load_config(dry_run: bool = False) -> Config:
    return Config(
        base_url=os.environ.get("META_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=os.environ.get("META_MODEL", DEFAULT_MODEL),
        api_key=os.environ.get("MODEL_API_KEY") or None,
        http_timeout_s=float(os.environ.get("PIPELINE_HTTP_TIMEOUT", "90")),
        max_tool_rounds=int(os.environ.get("PIPELINE_MAX_TOOL_ROUNDS", "8")),
        dry_run=dry_run,
    )
