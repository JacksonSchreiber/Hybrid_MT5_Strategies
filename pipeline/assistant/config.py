"""config.py — configuration + the auth launcher for the blind advisory app.

Implements the §8b directive: run on the Claude **subscription** by default (via
the Claude Agent SDK under CLAUDE_CODE_OAUTH_TOKEN), with the pay-per-token API
as an explicit fallback.

THE CRITICAL GOTCHA (§8b): in the auth precedence, a set ANTHROPIC_API_KEY
*outranks* the subscription OAuth token and is used automatically in headless
mode with no prompt — silently billing the API account per token. So on the
agent_sdk (subscription) transport the launcher **removes ANTHROPIC_API_KEY from
the environment** before any model call, and logs which auth path is active.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PLAYBOOK_DIR = REPO / "playbook"
PROMPTS_DIR = PLAYBOOK_DIR / "prompts"
LOG_DIR = REPO / "logs" / "assistant"

# secrets live OUTSIDE the repo (same convention as ~/.fmp_api_key)
OAUTH_TOKEN_FILE = Path(os.path.expanduser("~/.claude_code_oauth_token"))
API_KEY_FILE = Path(os.path.expanduser("~/.anthropic_api_key"))

# Agent SDK model aliases (subscription selects by alias, not dated id);
# the API transport uses the exact ids.
MODEL_ALIAS = {"tier1": "sonnet", "tier2": "fable"}
MODEL_API_ID = {"tier1": "claude-sonnet-5", "tier2": "claude-fable-5"}

VALID_TRANSPORTS = ("agent_sdk", "api")


@dataclass
class Config:
    transport: str = "agent_sdk"     # ASSISTANT_TRANSPORT env overrides
    news_horizon_h: float = 12.0
    tier1_img_max_px: int = 1568     # downscale for latency/cost
    tier2_img_max_px: int = 2576
    excerpt_budget_tokens: int = 4000
    log_path: Path = LOG_DIR / "decisions.jsonl"


def load_config() -> Config:
    t = os.environ.get("ASSISTANT_TRANSPORT", "agent_sdk").strip().lower()
    if t not in VALID_TRANSPORTS:
        sys.exit(f"ASSISTANT_TRANSPORT must be one of {VALID_TRANSPORTS}, got {t!r}")
    return Config(transport=t)


@dataclass
class AuthInfo:
    path: str            # human-readable active auth path
    transport: str
    subscription: bool
    api_key_removed: bool


def setup_auth(transport: str, *, verbose: bool = True) -> AuthInfo:
    """Prepare the process environment for the chosen transport and return which
    auth path is active. MUST be called once at startup before any model call."""
    if transport == "agent_sdk":
        # 1) remove the billing-flip trap
        removed = os.environ.pop("ANTHROPIC_API_KEY", None) is not None
        # 2) ensure the subscription OAuth token is present
        tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        if not tok:
            if not OAUTH_TOKEN_FILE.exists():
                sys.exit(
                    "ERROR: subscription transport selected but no OAuth token.\n"
                    f"  Run `claude setup-token` and save it to {OAUTH_TOKEN_FILE}\n"
                    "  (chmod 600), or set CLAUDE_CODE_OAUTH_TOKEN, or use "
                    "ASSISTANT_TRANSPORT=api.")
            tok = OAUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = tok
        # extend the prompt-cache TTL 5m → 1h (spec §3): decisions in a session are
        # often >5 min apart, so a 1h window is what makes repeat calls cache-read.
        os.environ.setdefault("ENABLE_PROMPT_CACHING_1H", "1")
        info = AuthInfo(
            path=f"SUBSCRIPTION via Claude Agent SDK (CLAUDE_CODE_OAUTH_TOKEN, {tok[:14]}…)",
            transport=transport, subscription=True, api_key_removed=removed)
    elif transport == "api":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key and API_KEY_FILE.exists():
            key = API_KEY_FILE.read_text(encoding="utf-8").strip()
            os.environ["ANTHROPIC_API_KEY"] = key
        if not key:
            sys.exit("ERROR: transport=api needs ANTHROPIC_API_KEY (env or "
                     f"{API_KEY_FILE}).")
        info = AuthInfo(path="PAY-PER-TOKEN via Anthropic API (ANTHROPIC_API_KEY)",
                        transport=transport, subscription=False, api_key_removed=False)
    else:  # pragma: no cover
        sys.exit(f"unknown transport {transport!r}")

    if verbose:
        print(f"[auth] active path: {info.path}", file=sys.stderr)
        if transport == "agent_sdk" and info.api_key_removed:
            print("[auth] removed ANTHROPIC_API_KEY from env (would have flipped "
                  "billing to pay-per-token).", file=sys.stderr)
    return info
