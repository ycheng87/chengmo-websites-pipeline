"""CLI 入口：python -m pipeline --merchant "店名" --city "城市" [--dry-run]"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .events import EventLog
from .run import run_pipeline
from .steps import STEP_NAMES

_INDENT = " " * 17


def _line(name: str, body: str) -> str:
    return f"{name:<15} {body}"


def format_event(name: str, data: dict) -> str:
    if name == "pipeline:start":
        return _line(name, f"runId={data['runId']}  merchant={data['merchant']}  startedAt={data['startedAt']}")
    if name == "step:start":
        return _line(name, f"step={data['step']}  ({STEP_NAMES.get(data['step'], '?')})")
    if name == "research:item":
        flag = "ok" if data["ok"] else "FAIL"
        body = f"[{data['index']}] {data['kind']:<8} {flag}  {data['text']}"
        if data.get("warn"):
            body += f"  warn: {data['warn']}"
        if data.get("sourceUrl"):
            body += f"  src={data['sourceUrl']}"
        return _line(name, body)
    if name == "site:block":
        return _line(name, f"key={data['key']}")
    if name == "site:ready":
        # 控制台不刷屏：html 只在 jsonl 里给全量
        return _line(name, f"url={data['url']}  bytes={data['bytes']}  html=<{data['bytes']} bytes，完整内容见 jsonl>")
    if name == "email:draft":
        body = data["body"].replace("\n", f"\n{_INDENT}")
        return _line(name, f"kind={data['kind']}  to={data['to']}\n"
                           f"{_INDENT}subject={data['subject']}\n"
                           f"{_INDENT}body: {body}")
    if name == "llm:usage":
        body = (f"step={data['step']}  model={data['model']}  "
                f"in={data['promptTokens']}  out={data['completionTokens']}  "
                f"reasoning={data['reasoningTokens']}  total={data['totalTokens']}  "
                f"tools={data['toolCalls']}  {data['durationMs']}ms")
        if data.get("textOnly"):
            body += "  ⚠ 只回文本未调工具"
        return _line(name, body)
    if name == "score:factor":
        return _line(name, f"[{data['index']}] {data['label']}  {data['points']}/{data['max']}")
    if name == "score:total":
        return _line(name, f"total={data['total']}  band={data['band']}  verdict={data['verdict']}")
    if name == "score:rationale":
        return _line(name, data["text"])
    if name == "step:done":
        return _line(name, f"step={data['step']}  durationMs={data['durationMs']}")
    if name == "pipeline:done":
        return _line(name, f"elapsedMs={data['elapsedMs']}  score={data['score']}")
    if name == "pipeline:error":
        return _line(name, f"step={data['step']}  recoverable={data['recoverable']}  message={data['message']}")
    return _line(name, json.dumps(data, ensure_ascii=False))


async def _amain(args: argparse.Namespace, config) -> None:
    print(f"# merchant-pipeline v{__version__}  {config.summary()}", flush=True)
    log: EventLog | None = None
    jsonl_path: Path | None = None
    run_id = None
    try:
        async for name, data in run_pipeline(args.merchant, args.city, config,
                                             runs_dir=args.runs_dir):
            if log is None and name == "pipeline:start":
                run_id = data["runId"]
                jsonl_path = Path(args.runs_dir) / f"{run_id}.jsonl"
                log = EventLog(jsonl_path)
            if log is not None:
                log.write(name, data)
            print(format_event(name, data), flush=True)
    except KeyboardInterrupt:
        print("\n# 已被用户中断（Ctrl+C）", flush=True)
        raise SystemExit(130)
    finally:
        if log is not None:
            log.close()
    if jsonl_path is not None:
        print(f"# 事件流已写入 {jsonl_path}", flush=True)
        print(f"# 产物目录 {Path(args.runs_dir) / run_id / 'artifacts'}"
              f"（site.html / emails/ / notes/）", flush=True)


def main(argv: list[str] | None = None) -> None:
    if sys.version_info < (3, 10):
        sys.exit("需要 Python 3.10+")
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="小商户自动化 agent v0.1：research → demo site → outreach → follow-up（事件流输出）",
    )
    parser.add_argument("--merchant", required=True, help="目标商户名，例如 \"老王煎饼铺\"")
    parser.add_argument("--city", required=True, help="城市，例如 \"杭州\"")
    parser.add_argument("--dry-run", action="store_true",
                        help="用内置 mock 数据跑通全流程，不调 LLM API（无需 key）")
    parser.add_argument("--runs-dir", default="runs", help="事件流与产物的输出目录（默认 runs/）")
    args = parser.parse_args(argv)

    try:  # Windows 控制台也可能是 GBK，强制 UTF-8 输出
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    config = load_config(dry_run=args.dry_run)
    asyncio.run(_amain(args, config))


if __name__ == "__main__":
    main()
