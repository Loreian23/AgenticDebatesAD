#!/usr/bin/env python3
"""LLM-powered judge hook for AgentDebate worker.

Usage with agent_worker.py:
  --judge-command "python3 scripts/judge_llm_hook.py --model deepseek/deepseek-v4-pro"

Reads task JSON from stdin, calls an LLM with a structured scoring prompt,
and prints the score JSON to stdout.

Requirements:
  - OpenClaw gateway running on localhost:18789
  - Auth token from ~/.openclaw/openclaw.json
"""

import json
import sys
import urllib.request
import os


def get_api_credentials() -> tuple:
    """Get provider API credentials from OpenClaw config."""
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    with open(config_path) as f:
        cfg = json.load(f)
    provider = cfg["models"]["providers"]["deepseek"]
    return provider["baseUrl"], provider["apiKey"]


def call_llm(prompt: str, model: str = "deepseek/deepseek-v4-pro", temperature: float = 0.2) -> str:
    """Call the LLM API directly via provider credentials from openclaw.json."""
    base_url, api_key = get_api_credentials()
    model_id = model.split("/")[-1] if "/" in model else model

    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a debate judge. Score each debater on the 5-criterion rubric "
                    "(argument_quality, evidence_quality, rebuttal_strength, clarity, compliance) "
                    "on a 0-10 scale. Use the full scale — 5 is average, 8 is strong, 10 is exceptional. "
                    "Base scores only on the debate content provided. Anchor your rationale to specific "
                    "claims and turns. Return ONLY valid JSON, no other text."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": 2048,
    }

    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
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


def build_judge_prompt(task: dict) -> str:
    payload = task.get("payload", {})
    target = payload.get("target_participant_name", "unknown")
    proposition = payload.get("proposition", "Unknown proposition")
    target_turns = payload.get("target_recent_turns", [])
    recent_turns = payload.get("recent_turns", [])
    rubric = payload.get("rubric", [
        "argument_quality", "evidence_quality", "rebuttal_strength",
        "clarity", "compliance",
    ])
    guidelines = payload.get("rubric_guidelines", {})
    instructions = payload.get("scoring_instructions", {})
    phase = task.get("phase", "judging")

    # Build the prompt
    parts = []

    parts.append(f"# Debate Judging Task")
    parts.append(f"")
    parts.append(f"**Proposition:** {proposition}")
    parts.append(f"**Debater to score:** {target}")
    parts.append(f"**Phase:** {phase}")
    parts.append(f"")

    parts.append("## Scoring Rubric")
    parts.append("")
    parts.append("Score each criterion 0-10. Use the full scale:")
    parts.append("- 1-2: Severely deficient")
    parts.append("- 3-4: Below average, significant gaps")
    parts.append("- 5-6: Competent but unexceptional")
    parts.append("- 7-8: Strong, well-executed")
    parts.append("- 9-10: Exceptional, nearly flawless")
    parts.append("")

    for criterion in rubric:
        guide = guidelines.get(criterion, "Score based on debate content.")
        parts.append(f"### {criterion.replace('_', ' ').title()}")
        parts.append(f"{guide}")
        parts.append("")

    rules = instructions.get("rules", [])
    if rules:
        parts.append("## Scoring Rules")
        for r in rules:
            parts.append(f"- {r}")
        parts.append("")

    parts.append("## Target Debater's Turns")
    parts.append("")
    for i, turn in enumerate(target_turns):
        side = turn.get("side", "?")
        content = turn.get("content", "")
        phase_label = turn.get("phase", "?").replace("_", " ").title()
        parts.append(f"### Turn {i + 1} — {phase_label} ({side.upper()})")
        parts.append("")
        parts.append(content)
        parts.append("")

    if recent_turns:
        parts.append("## Full Debate Context (All Participants)")
        parts.append("")
        for i, turn in enumerate(recent_turns):
            name = turn.get("participant_name", "?")
            side = turn.get("side", "?")
            content = turn.get("content", "")
            phase_label = turn.get("phase", "?").replace("_", " ").title()
            parts.append(f"### {name} ({side.upper()}) — {phase_label}")
            parts.append("")
            parts.append(content[:500] + ("..." if len(content) > 500 else ""))
            parts.append("")

    parts.append("## Output Format")
    parts.append("")
    parts.append("Return ONLY valid JSON, no markdown or explanation:")
    parts.append("```json")
    parts.append(json.dumps({
        "argument_quality": 0,
        "evidence_quality": 0,
        "rebuttal_strength": 0,
        "clarity": 0,
        "compliance": 0,
        "rationale": "2-5 sentences referencing specific strengths/weaknesses",
        "strengths": ["point 1", "point 2"],
        "weaknesses": ["gap 1", "gap 2"],
    }, indent=2))
    parts.append("```")

    return "\n".join(parts)


def extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        # Find the end of the first code block
        lines = text.split("\n")
        content_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```"):
                if not in_block:
                    in_block = True
                    continue
                else:
                    break
            if in_block:
                content_lines.append(line)
        if content_lines:
            text = "\n".join(content_lines)

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    import re
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Last resort: find balanced braces
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break

    raise RuntimeError(f"Could not extract valid JSON from LLM response: {text[:300]}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LLM judge hook for AgentDebate")
    parser.add_argument("--model", default="deepseek/deepseek-v4-pro", help="LLM model to use")
    parser.add_argument("--temperature", type=float, default=0.2, help="LLM temperature")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without calling LLM")
    args = parser.parse_args()

    # Read task from stdin
    raw = sys.stdin.read()
    task = json.loads(raw)

    prompt = build_judge_prompt(task)

    if args.dry_run:
        print(prompt, file=sys.stderr)
        # Return a placeholder
        print(json.dumps({
            "argument_quality": 5,
            "evidence_quality": 5,
            "rebuttal_strength": 5,
            "clarity": 5,
            "compliance": 5,
            "rationale": "Dry run — no LLM called.",
            "strengths": ["N/A"],
            "weaknesses": ["N/A"],
        }))
        return

    response = call_llm(prompt, model=args.model, temperature=args.temperature)
    score = extract_json(response)

    # Validate required fields
    required = ["argument_quality", "evidence_quality", "rebuttal_strength", "clarity", "compliance"]
    for field in required:
        if field not in score:
            score[field] = 5
        score[field] = max(0, min(10, int(score[field])))

    if "rationale" not in score:
        score["rationale"] = "No rationale provided."
    if "strengths" not in score or not isinstance(score["strengths"], list):
        score["strengths"] = []
    if "weaknesses" not in score or not isinstance(score["weaknesses"], list):
        score["weaknesses"] = []

    print(json.dumps(score))


if __name__ == "__main__":
    main()
