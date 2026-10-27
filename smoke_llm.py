#!/usr/bin/env python3
"""H-01 冒烟 B：1 次带 tool calling 的 LLM 调用（**只跑 1 次，不预烧**）。

验四件事：① MODEL_API_KEY 通 ② muse-spark-1.3 可用 ③ 支持 OpenAI 兼容 tool calling
④ 中文 prompt 下能正确发起工具调用。用量从 response.usage 读并落盘。

用法：MODEL_API_KEY=sk-... python3 smoke_llm.py [--json]
不加 key 会直接报错退出（不静默）。
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
MODEL = os.environ.get("META_MODEL", "muse-spark-1.3")
KEY = os.environ.get("MODEL_API_KEY", "").strip()

TOOLS = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索网页，返回标题/链接/摘要",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索词"}},
            "required": ["query"],
        },
    },
}]


def main() -> int:
    if not KEY:
        print("ERROR: 未设置 MODEL_API_KEY（H-01 需要阿成 10-26 交来的 key）")
        return 2
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是调研助手。需要外部信息时必须调用 web_search 工具，不要凭记忆回答。"},
            {"role": "user", "content": "查一下 Austin TX 一家做汽车贴膜的店最近的口碑情况，先搜索。"},
        ],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{BASE.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        print(f"HTTP_ERROR {e.code}: {e.read().decode('utf-8','ignore')[:500]}")
        return 1
    ms = int((time.monotonic() - t0) * 1000)
    usage = body.get("usage", {})
    msg = (body.get("choices") or [{}])[0].get("message", {})
    calls = msg.get("tool_calls") or []
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": body.get("model", MODEL),
        "base_url": BASE,
        "elapsed_ms": ms,
        "usage": usage,
        "tool_calls": [{"name": c.get("function", {}).get("name"),
                        "arguments": c.get("function", {}).get("arguments")} for c in calls],
        "content_head": (msg.get("content") or "")[:300],
    }
    os.makedirs("/home/ecs-user/hackathon/h02/smoke_runs", exist_ok=True)
    path = f"/home/ecs-user/hackathon/h02/smoke_runs/llm_smoke_{time.strftime('%Y%m%d_%H%M%S')}.json"
    open(path, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
    if "--json" in sys.argv:
        print(json.dumps(rec, ensure_ascii=False, indent=1))
    else:
        print(f"MODEL: {rec['model']}  ELAPSED_MS: {ms}")
        print(f"USAGE: {usage}")
        print(f"TOOL_CALLS: {rec['tool_calls']}")
        print(f"CONTENT: {rec['content_head']}")
    ok = bool(calls) and (calls[0].get("function", {}).get("name") == "web_search")
    print(f"VERDICT: {'PASS' if ok else 'FAIL — 模型没发起工具调用，tool calling 通道不通'}")
    print(f"RECORD: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
