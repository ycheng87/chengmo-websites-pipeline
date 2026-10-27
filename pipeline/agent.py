"""tool-calling 循环 + 两种 chat 后端（真跑 OpenAI 兼容接口 / dry-run 剧本回放）。

唯一的 LLM 供应商入口是 OpenAIChat：OpenAI 兼容接口，base_url / model / key
全部来自环境变量（见 config.py）。dry-run 用 ScriptedChat 替换"模型"本身，
循环、工具执行、提交校验的代码路径与真跑完全一致。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .config import Config
from .tools import ToolBox


class AgentError(RuntimeError):
    """工具循环无法继续（API 失败、模型拒绝调用工具、超过轮数上限等）。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantMessage:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[AssistantMessage]]


class ToolLoopAgent:
    """驱动模型走 tool-calling 循环，直到它调用本步骤的 submit_* 工具为止。"""

    def __init__(self, chat: ChatFn, toolbox: ToolBox, max_rounds: int = 8) -> None:
        self._chat = chat
        self._toolbox = toolbox
        self._max_rounds = max_rounds

    async def run(
        self,
        *,
        system: str,
        task: str,
        extra_tools: list[dict[str, Any]],
        submit_tool: dict[str, Any],
        validate: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        submit_name = submit_tool["function"]["name"]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        tools = extra_tools + [submit_tool]
        nudged = False
        for _round in range(self._max_rounds):
            msg = await self._chat(messages, tools)
            messages.append(_to_openai_assistant(msg))
            if not msg.tool_calls:
                if not nudged:
                    nudged = True
                    messages.append({
                        "role": "user",
                        "content": f"不要直接回复文本；请调用 {submit_name} 工具提交结构化结果。",
                    })
                    continue
                raise AgentError(f"模型连续未调用工具（期望 {submit_name}），步骤失败")
            for tc in msg.tool_calls:
                if tc.name == submit_name:
                    if validate is not None:
                        try:
                            validate(tc.arguments)
                        except ValueError as e:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": f"TOOL_ERROR: 提交被拒绝：{e}。请修正后重新调用 {submit_name}。",
                            })
                            continue
                    return tc.arguments
                result = await self._toolbox.execute(tc.name, tc.arguments)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        raise AgentError(f"超过最大工具轮数 {self._max_rounds}，仍未调用 {submit_name}")


def _to_openai_assistant(msg: AssistantMessage) -> dict[str, Any]:
    m: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        m["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name,
                             "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
            }
            for tc in msg.tool_calls
        ]
    return m


def _usage_get(holder: Any, name: str) -> int:
    """从 usage 对象/字典里取一个整数字段（缺字段当 0，provider 各家字段名不一致）。"""
    if holder is None:
        return 0
    value = holder.get(name) if isinstance(holder, dict) else getattr(holder, name, None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class OpenAIChat:
    """OpenAI 兼容接口封装（唯一的 LLM 供应商）。openai 包按需导入，dry-run 不依赖。"""

    def __init__(self, config: Config) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise AgentError("未安装 openai 包：请先 pip install -r requirements.txt") from e
        if not config.api_key:
            raise AgentError("缺少 MODEL_API_KEY 环境变量（dry-run 不需要 key）")
        self._model = config.model
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.http_timeout_s,
            max_retries=1,
        )
        # 用量账本：每次 HTTP 调用一行，由 run.py 取走后落进 jsonl（llm:usage 事件）
        self.step: int = -1
        self.records: list[dict[str, Any]] = []

    def drain_usage(self) -> list[dict[str, Any]]:
        """取走并清空累计的用量记录（run.py 在每步边界调用）。"""
        out, self.records = self.records, []
        return out

    async def __call__(self, messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]]) -> AssistantMessage:
        t0 = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                tool_choice="auto",
            )
        except Exception as e:
            raise AgentError(
                f"LLM 调用失败（{self._model} @ {getattr(self._client, 'base_url', '')}）："
                f"{type(e).__name__}: {e}"
            ) from e
        choice = resp.choices[0] if resp.choices else None
        message = getattr(choice, "message", None) if choice is not None else None
        tool_calls: list[ToolCall] = []
        for c in getattr(message, "tool_calls", None) or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": c.function.arguments}
            tool_calls.append(ToolCall(id=c.id or f"call-{len(tool_calls)}",
                                       name=c.function.name, arguments=args))
        usage = getattr(resp, "usage", None)
        prompt_tok = _usage_get(usage, "prompt_tokens")
        completion_tok = _usage_get(usage, "completion_tokens")
        reasoning_tok = _usage_get(getattr(usage, "completion_tokens_details", None),
                                   "reasoning_tokens")
        self.records.append({
            "step": self.step,
            "model": getattr(resp, "model", None) or self._model,
            "promptTokens": prompt_tok,
            "completionTokens": completion_tok,
            "reasoningTokens": reasoning_tok,
            "totalTokens": _usage_get(usage, "total_tokens") or (prompt_tok + completion_tok),
            "durationMs": int((time.monotonic() - t0) * 1000),
            "toolCalls": len(tool_calls),
            "textOnly": not tool_calls,
        })
        return AssistantMessage(content=getattr(message, "content", None), tool_calls=tool_calls)


class ScriptedChat:
    """dry-run 用：按剧本回放带 tool_calls 的助手消息，走与真实客户端相同的循环。"""

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]]]) -> None:
        self._turns = list(turns)
        self._i = 0

    async def __call__(self, messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]]) -> AssistantMessage:
        if self._i >= len(self._turns):
            raise AgentError("dry-run 剧本已耗尽（该步骤没有提交结果）")
        calls = [ToolCall(id=f"call-{self._i}-{j}", name=name, arguments=dict(args))
                 for j, (name, args) in enumerate(self._turns[self._i])]
        self._i += 1
        return AssistantMessage(content=None, tool_calls=calls)
