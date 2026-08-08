"""transport.py — pluggable model transport (the §8b interface).

  agent_sdk (default): Claude Agent SDK under the subscription OAuth token. Vision
      requires ClaudeSDKClient streaming input (not single-shot query()) — confirmed
      against claude-agent-sdk 0.2.134. Tools are disabled so the call is a pure
      text/vision judgment with no agent loop. usage (incl. cache tokens) comes off
      the ResultMessage.
  api (fallback): the Anthropic Messages API (pay-per-token), base64 image blocks +
      explicit 1h cache_control on the cached system prefix.

Each transport exposes one SYNC method, `judge(...)`, that returns a ModelReply.
The agent_sdk path runs its async work in a fresh event loop per call (a single
event loop with two back-to-back ClaudeSDKClient sessions can wedge on subprocess
teardown — a fresh asyncio.run per call avoids it).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

# Everything the harness might otherwise run — off, so the model can't tool-loop.
_DISABLE_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch",
                  "WebFetch", "Task", "NotebookEdit", "TodoWrite", "Agent"]


@dataclass
class ModelReply:
    text: str
    usage: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def cache_read(self) -> int:
        return int(self.usage.get("cache_read_input_tokens") or 0)

    @property
    def cache_creation(self) -> int:
        return int(self.usage.get("cache_creation_input_tokens") or 0)


def _img_block(b64: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64}}


class AgentSdkTransport:
    """Subscription transport via the Claude Agent SDK."""

    def __init__(self, alias_map: dict):
        self.alias = alias_map          # {"tier1":"sonnet","tier2":"fable"}

    def judge(self, *, system: str, images_b64: list[str], user_text: str,
              tier: str, timeout: float = 180.0) -> ModelReply:
        return asyncio.run(self._judge(system, images_b64, user_text, tier, timeout))

    async def _judge(self, system, images_b64, user_text, tier, timeout) -> ModelReply:
        from claude_agent_sdk import (ClaudeSDKClient, ClaudeAgentOptions,
                                      AssistantMessage, ResultMessage, TextBlock)
        kw = dict(
            model=self.alias[tier],
            system_prompt=system,                 # byte-stable → server-side cache hits
            disallowed_tools=_DISABLE_TOOLS,
            permission_mode="bypassPermissions",
        )
        # latency levers (spec §6): Tier 1 (Sonnet) runs thinking-OFF + effort low
        # for the <8s target; Tier 2 (Fable) must OMIT thinking (explicit config
        # 400s on Fable) and runs effort medium. thinking is a TypedDict on this
        # SDK — pass the {"type": ...} shape, not the empty-constructed class.
        if tier == "tier1":
            kw["thinking"] = {"type": "disabled"}
            kw["effort"] = "low"
        else:
            kw["effort"] = "medium"
        opts = ClaudeAgentOptions(**kw)
        content = [_img_block(b) for b in images_b64]
        content.append({"type": "text", "text": user_text})

        async def gen():
            yield {"type": "user",
                   "message": {"role": "user", "content": content}}

        parts: list[str] = []
        usage: dict = {}
        stop: str | None = None

        async def run():
            nonlocal usage, stop
            async with ClaudeSDKClient(options=opts) as c:
                await c.query(gen())
                async for m in c.receive_response():
                    if isinstance(m, AssistantMessage):
                        for blk in m.content:
                            if isinstance(blk, TextBlock):
                                parts.append(blk.text)
                    elif isinstance(m, ResultMessage):
                        usage = m.usage or {}
                        stop = getattr(m, "subtype", None)

        try:
            await asyncio.wait_for(run(), timeout=timeout)
        except asyncio.TimeoutError:
            return ModelReply("".join(parts), usage, error=f"timeout>{timeout:.0f}s")
        except Exception as e:                     # SDK raises after error results
            return ModelReply("".join(parts), usage, error=repr(e))
        err = None if (stop in (None, "success")) else f"stop={stop}"
        return ModelReply("".join(parts), usage, error=err)


class ApiTransport:
    """Fallback transport via the Anthropic Messages API (pay-per-token)."""

    def __init__(self, id_map: dict):
        self.ids = id_map               # {"tier1":"claude-sonnet-5","tier2":"claude-fable-5"}

    def judge(self, *, system: str, images_b64: list[str], user_text: str,
              tier: str, timeout: float = 180.0) -> ModelReply:
        import anthropic
        client = anthropic.Anthropic()
        content = [_img_block(b) for b in images_b64]
        content.append({"type": "text", "text": user_text})
        kwargs = dict(
            model=self.ids[tier],
            max_tokens=1024,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            messages=[{"role": "user", "content": content}],
        )
        if tier == "tier1":
            kwargs["thinking"] = {"type": "disabled"}      # latency; Sonnet-5 defaults adaptive
            kwargs["output_config"] = {"effort": "low"}
        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:
            return ModelReply("", {}, error=repr(e))
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text")
        u = resp.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        err = None if resp.stop_reason != "refusal" else "refusal"
        return ModelReply(text, usage, error=err)


def make_transport(transport: str, alias_map: dict, id_map: dict):
    if transport == "agent_sdk":
        return AgentSdkTransport(alias_map)
    if transport == "api":
        return ApiTransport(id_map)
    raise ValueError(f"unknown transport {transport!r}")
