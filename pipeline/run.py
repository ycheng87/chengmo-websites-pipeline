"""pipeline 主编排：async generator，逐个 yield 事件。

每一步失败都会 yield pipeline:error（可恢复的降级继续跑，不可恢复的终止），
外层还有一层兜底 net：连内部 bug 也会变成 pipeline:error，绝不静默卡死。
"""
from __future__ import annotations

import secrets
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse


class FatalPipelineError(RuntimeError):
    """不可恢复：带病终止，不再 yield pipeline:done（灵犀 10-24 定的前置闸）。"""


# 🔴 外发邮件里的链接只允许这些域名（小恰 H-03 复核指出：只验 https:// 前缀是形式验证）
ALLOWED_LINK_HOSTS = {"chengmo.top"}


def _check_outbound_link(body: str) -> None:
    """外发邮件链接闸：必须是 https + 合法主机名 + 域名白名单。不过就抛 FatalPipelineError。"""
    urls = re.findall(r"https?://[^\s\u3000）)】」\"'<>]+", body)
    if not urls:
        raise FatalPipelineError("正文里没有任何链接（必须有可访问的 demo 地址）")
    for raw in urls:
        u = urlparse(raw)
        if u.scheme != "https":
            raise FatalPipelineError(f"链接不是 https：{raw}")
        host = u.hostname or ""
        if not host or "." not in host:
            raise FatalPipelineError(f"链接没有合法主机名：{raw}")
        if host not in ALLOWED_LINK_HOSTS:
            raise FatalPipelineError(
                f"链接域名不在白名单（{host}，只允许 {sorted(ALLOWED_LINK_HOSTS)}）：{raw}")

from . import mock as mock_mod
from . import site as site_mod
from . import steps
from .agent import AgentError, OpenAIChat, ScriptedChat, ToolLoopAgent
from .config import Config
from .events import (
    EMAIL_DRAFT,
    LLM_USAGE,
    PIPELINE_DONE,
    PIPELINE_ERROR,
    PIPELINE_START,
    RESEARCH_ITEM,
    SCORE_FACTOR,
    SCORE_RATIONALE,
    SCORE_TOTAL,
    SITE_BLOCK,
    SITE_READY,
    STEP_DONE,
    STEP_START,
    event,
)
from .tools import ToolBox


def _err(e: BaseException) -> str:
    msg = str(e).strip() or e.__class__.__name__
    return f"{e.__class__.__name__}: {msg}"


def _min_research_ok() -> int:
    """research 不足即停阈值（默认 2/5），MIN_RESEARCH_OK 环境变量可调。"""
    import os
    try:
        v = int(os.environ.get("MIN_RESEARCH_OK", "2"))
        return max(0, min(5, v))
    except ValueError:
        return 2


def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def run_pipeline(merchant: str, city: str, config: Config,
                       runs_dir: str | Path = "runs") -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """对外入口：async generator，逐个 yield (事件名, 载荷)。"""
    state = {"step": -1}
    inner = _run(merchant, city, config, Path(runs_dir), state)
    while True:
        try:
            item = await inner.__anext__()
        except StopAsyncIteration:
            break
        except Exception as e:  # 兜底：内部 bug 也必须变成 pipeline:error
            yield event(PIPELINE_ERROR, step=state["step"], message=_err(e), recoverable=False)
            break
        yield item


async def _run(merchant: str, city: str, config: Config, runs_dir: Path,
               state: dict[str, int]):
    t0 = time.monotonic()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    yield event(PIPELINE_START, runId=run_id, merchant=merchant, startedAt=started_at)

    run_dir = runs_dir / run_id
    mock_sources = mock_mod.build_mock_sources(merchant, city) if config.dry_run else None
    toolbox = ToolBox(run_dir / "artifacts", mock_sources=mock_sources,
                      http_timeout_s=config.http_timeout_s)

    chat = None
    if not config.dry_run:
        try:
            chat = OpenAIChat(config)
        except AgentError as e:
            yield event(PIPELINE_ERROR, step=0, message=str(e), recoverable=False)
            return

    def make_agent(script_turns: list | None, step: int) -> ToolLoopAgent:
        backend = ScriptedChat(script_turns) if chat is None else chat
        if chat is not None:
            chat.step = step
        return ToolLoopAgent(backend, toolbox, max_rounds=config.max_tool_rounds)

    def take_usage() -> list[tuple[str, dict[str, Any]]]:
        """把本步已发生的 LLM 调用用量转成 llm:usage 事件（失败路径也要落账）。"""
        if chat is None:
            return []
        return [event(LLM_USAGE, **rec) for rec in chat.drain_usage()]

    research: list[dict] = []
    site_url = ""
    score_total: int | None = None

    # ---------------- step 0: research ----------------
    state["step"] = 0
    yield event(STEP_START, step=0)
    step_t0 = time.monotonic()
    try:
        script = mock_mod.research_script(mock_sources) if mock_sources else None
        agent = make_agent(script, 0)
        payload = await agent.run(
            system=steps.SYSTEM_PROMPT,
            task=steps.research_task(merchant, city),
            extra_tools=steps.RESEARCH_TOOLS,
            submit_tool=steps.SUBMIT_RESEARCH,
            validate=steps.validate_research,
        )
        for ev in take_usage():
            yield ev
        research = steps.validate_research(payload)
        ok_count = sum(1 for item in research if item.get("ok"))
        for item in research:
            yield event(RESEARCH_ITEM, index=item["index"], kind=item["kind"],
                        text=item["text"], ok=item["ok"], warn=item["warn"],
                        sourceUrl=item["sourceUrl"])
        yield event(STEP_DONE, step=0, durationMs=_ms_since(step_t0))
        # research 不足即停（灵犀 10-27 H-02 返工令）：ok < 阈值直接停在待核实，
        # 不进 site/email/score。阈值默认 2，可用 MIN_RESEARCH_OK 环境变量调。
        min_ok = _min_research_ok()
        if ok_count < min_ok:
            yield event(PIPELINE_ERROR, step=0,
                        message=(f"research 可用项 {ok_count}/5 < 阈值 {min_ok}："
                                 "停在待核实，不生成邮件、不打分"),
                        recoverable=False)
            return
    except Exception as e:
        research = []
        for ev in take_usage():
            yield ev
        yield event(PIPELINE_ERROR, step=0, message=_err(e), recoverable=True)
        yield event(STEP_DONE, step=0, durationMs=_ms_since(step_t0))
        return

    # ---------------- step 1: demo site ----------------
    state["step"] = 1
    yield event(STEP_START, step=1)
    step_t0 = time.monotonic()
    try:
        script = mock_mod.site_script(mock_sources, research) if mock_sources else None
        agent = make_agent(script, 1)
        payload = await agent.run(
            system=steps.SYSTEM_PROMPT,
            task=steps.site_task(merchant, city, research),
            extra_tools=steps.SITE_TOOLS,
            submit_tool=steps.SUBMIT_SITE,
            validate=steps.validate_site,
        )
        for ev in take_usage():
            yield ev
        blocks = steps.validate_site(payload)
        for key in steps.SITE_BLOCK_ORDER:
            yield event(SITE_BLOCK, key=key)
        site_html = site_mod.render_site(merchant, city, blocks)
        write_result = await toolbox.execute("write_file",
                                             {"path": "site.html", "content": site_html})
        if write_result.startswith("TOOL_ERROR"):
            raise RuntimeError(f"写入 site.html 失败: {write_result}")
        site_url = site_mod.public_url(merchant, city)
        yield event(SITE_READY, url=site_url, bytes=len(site_html.encode("utf-8")),
                    html=site_html)
    except Exception as e:
        site_url = ""
        for ev in take_usage():
            yield ev
        yield event(PIPELINE_ERROR, step=1, message=_err(e), recoverable=True)
    yield event(STEP_DONE, step=1, durationMs=_ms_since(step_t0))

    # ---------------- step 2: outreach emails ----------------
    state["step"] = 2
    yield event(STEP_START, step=2)
    step_t0 = time.monotonic()
    try:
        script = mock_mod.email_script(mock_sources, site_url) if mock_sources else None
        agent = make_agent(script, 2)
        payload = await agent.run(
            system=steps.SYSTEM_PROMPT,
            task=steps.email_task(merchant, city, research, site_url),
            extra_tools=steps.SITE_TOOLS,
            submit_tool=steps.SUBMIT_EMAILS,
            validate=steps.validate_emails,
        )
        for ev in take_usage():
            yield ev
        emails = steps.validate_emails(payload)
        for kind in ("first", "followup"):
            mail = emails[kind]
            # 🔴 前置闸：https + 合法主机名 + 域名白名单（不只是前缀匹配）
            try:
                _check_outbound_link(mail["body"])
            except FatalPipelineError as e:
                raise FatalPipelineError(f"email:draft[{kind}] {e}") from e
            await toolbox.execute("write_file", {
                "path": f"emails/{kind}.txt",
                "content": f"To: {mail['to']}\nSubject: {mail['subject']}\n\n{mail['body']}\n",
            })
            yield event(EMAIL_DRAFT, kind=kind, to=mail["to"],
                        subject=mail["subject"], body=mail["body"])
    except FatalPipelineError as e:
        for ev in take_usage():
            yield ev
        yield event(PIPELINE_ERROR, step=2, message=_err(e), recoverable=False)
        yield event(STEP_DONE, step=2, durationMs=_ms_since(step_t0))
        return
    except Exception as e:
        for ev in take_usage():
            yield ev
        yield event(PIPELINE_ERROR, step=2, message=_err(e), recoverable=True)
    yield event(STEP_DONE, step=2, durationMs=_ms_since(step_t0))

    # ---------------- step 3: followup + score ----------------
    state["step"] = 3
    yield event(STEP_START, step=3)
    step_t0 = time.monotonic()
    try:
        script = mock_mod.score_script(mock_sources, research) if mock_sources else None
        agent = make_agent(script, 3)
        payload = await agent.run(
            system=steps.SYSTEM_PROMPT,
            task=steps.score_task(merchant, city, research),
            extra_tools=[],
            submit_tool=steps.SUBMIT_SCORE,
            validate=steps.validate_score,
        )
        for ev in take_usage():
            yield ev
        result = steps.validate_score(payload)
        for factor in result["factors"]:
            yield event(SCORE_FACTOR, index=factor["index"], label=factor["label"],
                        points=factor["points"], max=factor["max"])
        score_total = result["total"]
        yield event(SCORE_TOTAL, total=result["total"], verdict=result["verdict"],
                    band=result["band"])
        yield event(SCORE_RATIONALE, text=result["rationale"])
    except Exception as e:
        for ev in take_usage():
            yield ev
        yield event(PIPELINE_ERROR, step=3, message=_err(e), recoverable=True)
    yield event(STEP_DONE, step=3, durationMs=_ms_since(step_t0))

    yield event(PIPELINE_DONE, elapsedMs=_ms_since(t0), score=score_total)
