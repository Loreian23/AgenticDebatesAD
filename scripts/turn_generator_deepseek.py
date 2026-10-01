#!/usr/bin/env python3
"""DeepSeek-powered turn generator for AgentDebate worker.
Replaces MiniMax turn_generator with DeepSeek v4 Pro via OpenClaw credentials.

Usage with agent_worker.py:
  --turn-command "python3 scripts/turn_generator_deepseek.py"

Reads task JSON from stdin, calls DeepSeek, prints turn text to stdout.
"""

import json
import os
import sys
import urllib.request
from typing import Any


def get_api_credentials() -> tuple:
    """Get DeepSeek API credentials from OpenClaw config."""
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    with open(config_path) as f:
        cfg = json.load(f)
    provider = cfg["models"]["providers"]["deepseek"]
    return provider["baseUrl"], provider["apiKey"]


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """Call DeepSeek chat completions API and return the response text."""
    base_url, api_key = get_api_credentials()

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())

    content = result["choices"][0]["message"]["content"]
    return content.strip()


def build_turn_prompt(task: dict[str, Any]) -> tuple[str, str]:
    """Build system + user prompts for turn generation from task JSON."""
    payload = task.get("payload", {}) or {}
    proposition = payload.get("proposition", "Unknown proposition")
    phase = task.get("phase", "opening")
    assigned_role = payload.get("assigned_role", "con")
    recent_turns = payload.get("recent_turns", []) or []

    # Determine opposing side
    opposing = "con" if assigned_role == "pro" else "pro"

    system = (
        f"You are the {assigned_role.upper()} side in a formal debate. "
        f"The proposition is: '{proposition}'. "
        f"Your role is to argue convincingly for the {assigned_role.upper()} position. "
        f"Provide well-reasoned arguments with evidence, examples, and clear logic. "
        f"Acknowledge counterarguments and address them. "
        f"Be persuasive but fair and intellectually honest."
    )

    # Build context from recent turns
    context = ""
    if recent_turns:
        context = "\n\nRecent debate turns:\n"
        for t in recent_turns:
            side = t.get("side", "unknown")
            text = t.get("content", "")[:500]
            context += f"\n--- {side.upper()} ---\n{text}\n"

    phase_instructions = {
        "opening": (
            "This is your OPENING STATEMENT. Make your strongest case. "
            "Present your main arguments, supporting evidence, and overall position. "
            "Set the framework for the debate."
        ),
        "rebuttal": (
            "This is a REBUTTAL. Directly address the opposing side's arguments. "
            "Point out logical flaws, counter their evidence, and reinforce your position."
        ),
        "closing": (
            "This is your CLOSING STATEMENT. Summarize your strongest points, "
            "reaffirm your position, and explain why your side has the stronger case."
        ),
    }

    instruction = phase_instructions.get(phase, f"Present your {phase} argument.")

    user = (
        f"Debate phase: {phase}\n"
        f"Your side: {assigned_role.upper()}\n"
        f"Proposition: {proposition}\n"
        f"{context}\n\n"
        f"{instruction}\n\n"
        f"Write a substantive debate turn (800-2000 characters) that advances the discussion."
    )

    return system, user


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        print("ERROR: No input provided", file=sys.stderr)
        sys.exit(1)

    try:
        task = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    system_prompt, user_prompt = build_turn_prompt(task)

    try:
        content = call_llm(system_prompt, user_prompt)
        print(content)
    except Exception as e:
        print(f"ERROR: LLM call failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
