"""事件契约：事件名与必带字段一个字都不能改；EventLog 负责落盘 runs/<runId>.jsonl。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PIPELINE_START = "pipeline:start"
STEP_START = "step:start"
RESEARCH_ITEM = "research:item"
SITE_BLOCK = "site:block"
SITE_READY = "site:ready"
EMAIL_DRAFT = "email:draft"
SCORE_FACTOR = "score:factor"
SCORE_TOTAL = "score:total"
SCORE_RATIONALE = "score:rationale"
LLM_USAGE = "llm:usage"
STEP_DONE = "step:done"
PIPELINE_DONE = "pipeline:done"
PIPELINE_ERROR = "pipeline:error"

# 每个事件的必带字段（score:factor 的 evidence 是功能需求额外要求的字段）
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    PIPELINE_START: ("runId", "merchant", "startedAt"),
    STEP_START: ("step",),
    RESEARCH_ITEM: ("index", "kind", "text", "ok", "warn", "sourceUrl"),
    SITE_BLOCK: ("key",),
    SITE_READY: ("url", "bytes", "html"),
    EMAIL_DRAFT: ("kind", "to", "subject", "body"),
    SCORE_FACTOR: ("index", "label", "points", "max"),
    SCORE_TOTAL: ("total", "verdict", "band"),
    SCORE_RATIONALE: ("text",),
    LLM_USAGE: ("step", "model", "promptTokens", "completionTokens",
                "reasoningTokens", "totalTokens", "durationMs", "toolCalls", "textOnly"),
    STEP_DONE: ("step", "durationMs"),
    PIPELINE_DONE: ("elapsedMs", "score"),
    PIPELINE_ERROR: ("step", "message", "recoverable"),
}


def event(name: str, **data: Any) -> tuple[str, dict[str, Any]]:
    """构造并校验一个事件（未知事件名或缺字段直接抛错，保证契约不被破坏）。"""
    if name not in REQUIRED_FIELDS:
        raise ValueError(f"未知事件名: {name!r}")
    missing = [f for f in REQUIRED_FIELDS[name] if f not in data]
    if missing:
        raise ValueError(f"事件 {name} 缺少字段: {missing}")
    return name, data


class EventLog:
    """把完整事件流追加写入 runs/<runId>.jsonl，一行一个事件。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, name: str, data: dict[str, Any]) -> None:
        record = {"ts": round(time.time(), 3), "event": name, "data": data}
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
