#!/usr/bin/env python3
"""H-01 冒烟 A：单独验 web_search（DuckDuckGo，零 token）。

灵犀附加条 B 要求：DDG 是整条 research 的单点，H-01 冒烟时先单独验它。
用法：python3 smoke_ddg.py ["查询词"]
判据：返回 >=1 条带 URL 的结果；否则按「降级预案」处理（research 只抓简报给定 URL）。
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/ecs-user/hackathon/h02")
from pipeline.tools import ToolBox  # noqa: E402


async def main() -> int:
    q = sys.argv[1] if len(sys.argv) > 1 else "Austin TX window tinting shop"
    run_dir = Path("/home/ecs-user/hackathon/h02/smoke_runs/ddg")
    run_dir.mkdir(parents=True, exist_ok=True)
    tb = ToolBox(run_dir)          # mock_sources=None → 真联网
    t0 = time.monotonic()
    out = await tb.execute("web_search", {"query": q})
    ms = int((time.monotonic() - t0) * 1000)
    print(f"QUERY: {q}")
    print(f"ELAPSED_MS: {ms}")
    print(f"RESULT_LEN: {len(out)}")
    print("--- RESULT (head 1200) ---")
    print(out[:1200])
    ok = out.startswith("TOOL_ERROR") is False and "http" in out and len(out) > 100
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
