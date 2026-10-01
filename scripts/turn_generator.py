#!/usr/bin/env python3
"""LLM-powered turn generator hook for AgentDebate worker using DeepSeek API.

Usage with agent_worker.py:
  --turn-command "python3 scripts/turn_generator.py"

Reads task JSON from stdin, calls DeepSeek to generate a debate
turn, and prints the turn text to stdout.

Requirements:
  - DeepSeek API credentials in ~/.openclaw/openclaw.json
  - Python 3.9+
"""

import json
import os
import re
import sys
import urllib.request
from typing import Any


def get_deepseek_credentials() -> tuple[str, str]:
    """Load DeepSeek API key and base URL from OpenClaw config."""
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    with open(config_path) as f:
        cfg = json.load(f)
    provider = cfg["models"]["providers"]["deepseek"]
    return provider["baseUrl"].rstrip("/"), provider["apiKey"]


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = "deepseek-v4-flash",
    temperature: float = 0.8,
    max_tokens: int = 4096,
) -> str:
    """Call DeepSeek chat completions API and return the response text."""
    api_base, api_key = get_deepseek_credentials()

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
        f"{api_base}/v1/chat/completions",
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
    agent_name = task.get("participant_name") or payload.get("agent_name", "Debater")
    content_mode = (payload.get("turn_quality_instructions") or {}).get(
        "content_mode", "rich"
    )

    # Length constraints
    max_len = payload.get("max_turn_length")
    if not isinstance(max_len, int) or max_len <= 0:
        max_len = 3000

    length_targets = (payload.get("turn_quality_instructions") or {}).get(
        "length_targets", {}
    )
    target_min = length_targets.get("target_min_chars", max(300, int(max_len * 0.6)))
    enforced_min = length_targets.get("enforced_min_chars", target_min)

    # Determine stance
    supports = assigned_role in ("pro",)
    stance_label = "PRO (affirming the proposition)" if supports else "CON (opposing the proposition)"

    # Phase descriptions
    phase_descriptions = {
        "opening": (
            "This is the OPENING statement. Present your core case clearly. "
            "Define your position, state your main arguments (2-4 pillars), "
            "and establish the framework you will use throughout the debate. "
            "Do not yet rebut specific opposing arguments — save that for rebuttal phases."
        ),
        "rebuttal_1": (
            "This is the FIRST REBUTTAL. Directly engage with the opponent's "
            "opening arguments. Identify their weakest claims, expose logical gaps, "
            "and offer counter-evidence. Strengthen your own position while attacking theirs."
        ),
        "rebuttal_2": (
            "This is the SECOND REBUTTAL. The debate is maturing. Address any "
            "new points raised in the first rebuttal. Deepen your analysis. "
            "Show how your position has held up better under scrutiny. "
            "Avoid merely repeating earlier arguments — add new depth or angles."
        ),
        "closing": (
            "This is the CLOSING STATEMENT. Summarize the debate. "
            "Identify the key points of disagreement and explain why your side "
            "prevailed on the most important ones. Do not introduce new arguments. "
            "Synthesize and crystallize. Leave the judge with a clear, memorable "
            "reason to rule in your favor."
        ),
        "cross_exam": (
            "This is a CROSS-EXAMINATION turn. Respond to targeted questions "
            "or challenges. Be precise, direct, and avoid evasion. "
            "Concede where appropriate to maintain credibility, "
            "then pivot to your strengths."
        ),
    }
    phase_guidance = phase_descriptions.get(phase, f"Debate phase: {phase}. Engage substantively.")

    # Content mode guidance
    content_mode_guidance = {
        "rich": (
            "Write in detail with evidence, examples, and reasoning. "
            "Use paragraph structure. Be thorough and substantive."
        ),
        "concise": (
            "Be concise and direct. Make every word count. "
            "Shorter sentences, tighter arguments, less elaboration."
        ),
        "balanced": (
            "Balance depth with readability. Provide enough detail to be credible "
            "without becoming verbose. Aim for clear, well-structured paragraphs."
        ),
    }
    mode_guidance = content_mode_guidance.get(content_mode, content_mode_guidance["balanced"])

    # Build system prompt
    system_prompt = (
        f"You are {agent_name}, a skilled debate participant in a formal structured debate. "
        f"Your role: {stance_label}. "
        f"Proposition under debate: \"{proposition}\"\n\n"
        f"DEBATE PHASE: {phase.upper()}\n"
        f"{phase_guidance}\n\n"
        f"CONTENT STYLE: {content_mode.upper()}\n"
        f"{mode_guidance}\n\n"
        f"RULES:\n"
        f"- Target length: {target_min}–{max_len} characters\n"
        f"- Stay in character as {agent_name} ({assigned_role.upper()} side)\n"
        f"- Engage with specific claims, not vague generalities\n"
        f"- Use evidence and reasoning, not mere assertion\n"
        f"- Acknowledge valid opposing points honestly, then explain why they don't prevail\n"
        f"- Write ONLY the turn content — no preamble, no meta-commentary, no markdown headers\n"
        f"- Do not sign your name at the end"
    )

    # Build user prompt with context
    parts = [f"Generate your {phase.upper()} turn as the {assigned_role.upper()} side."]

    if recent_turns:
        parts.append("\n## Recent Debate History\n")
        for i, turn in enumerate(recent_turns[-6:]):  # Last 6 turns for context
            side = turn.get("side", "?")
            name = turn.get("participant_name", "Unknown")
            content = turn.get("content", "")
            phase_label = turn.get("phase", "?").replace("_", " ").title()
            # Truncate long turns in history to save context window
            if len(content) > 600:
                content = content[:600] + "..."
            parts.append(
                f"### Turn {i + 1}: {name} ({side.upper()}) — {phase_label}\n{content}\n"
            )
    else:
        parts.append(
            "\nThis is the first turn of the debate. Set the tone and establish your position."
        )

    parts.append(
        f"\nNow write your {phase.upper()} turn. Remember: {target_min}–{max_len} characters, "
        f"{content_mode} style. Output only the turn content."
    )

    return system_prompt, "\n".join(parts)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="MiniMax-powered turn generator hook for AgentDebate"
    )
    parser.add_argument(
        "--model",
        default="deepseek-v4-flash",
        help="MiniMax model to use",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.8,
        help="LLM temperature (higher = more creative)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Maximum output tokens",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompt without calling API",
    )
    args = parser.parse_args()

    # Read task from stdin
    raw = sys.stdin.read()
    if not raw.strip():
        print("ERROR: No task JSON received on stdin", file=sys.stderr)
        return 1

    try:
        task = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid task JSON: {e}", file=sys.stderr)
        return 1

    system_prompt, user_prompt = build_turn_prompt(task)

    if args.dry_run:
        print("=== SYSTEM PROMPT ===", file=sys.stderr)
        print(system_prompt, file=sys.stderr)
        print("\n=== USER PROMPT ===", file=sys.stderr)
        print(user_prompt, file=sys.stderr)
        # Output placeholder for worker compatibility
        print("[DRY RUN — no API call made]")
        return 0

    try:
        content = call_llm(
            system_prompt,
            user_prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except Exception as e:
        print(f"ERROR: MiniMax API call failed: {e}", file=sys.stderr)
        return 1

    # Strip any markdown code blocks or wrapper text the model might add
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove opening fence
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence if present
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    # Strip common preamble patterns the model sometimes adds
    preamble_patterns = [
        r"^Here(?: is|'s) (?:my|the) .*?(?:turn|statement|argument)[:：]\s*",
        r"^My .*?(?:turn|statement|argument)[:：]\s*",
        r"^\(.*?(?:turn|statement|argument).*?\)\s*",
    ]
    for pattern in preamble_patterns:
        content = re.sub(pattern, '', content, count=1, flags=re.IGNORECASE).strip()

    # Strip trailing signatures
    signature_patterns = [
        r'\n\s*-{2,}\s*\n.*$',
        r'\n\s*—+\s*\n.*$',
        r'\n\s*Signed[,:]?\s*.*$',
    ]
    for pattern in signature_patterns:
        content = re.sub(pattern, '', content, flags=re.IGNORECASE).strip()

    if not content:
        print("ERROR: Generated empty content", file=sys.stderr)
        return 1

    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
