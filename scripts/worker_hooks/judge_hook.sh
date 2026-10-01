#!/usr/bin/env bash
# =============================================================================
# judge_hook.sh — Starter judge scoring hook for AgentDebate workers
# =============================================================================
# Usage:
#   judge_hook.sh <task_json>
#   stdin:  task JSON (see AgentTaskView schema)
#   stdout: judge score JSON
#
# Expected output format (JSON):
#   {
#     "argument_quality": <float 1-10>,
#     "evidence_quality": <float 1-10>,
#     "rebuttal_strength": <float 1-10>,
#     "clarity": <float 1-10>,
#     "compliance": <float 1-10>,
#     "overall_weighted": <float 1-10>,
#     "rationale": "<string>"
#   }
#
# Environment:
#   DEBATE_API_BASE  — base URL of the debate API (default: http://127.0.0.1:8000)
#   AGENT_MODEL      — model name to use (default: from OPENAI_MODEL env)
#   OPENAI_API_KEY   — your OpenAI API key
#
# Hook protocol:
#   1. Read task JSON from stdin
#   2. Extract: debate_id, all turns, proposition, assigned_side
#   3. Score each side on the 5 criteria using your LLM
#   4. Print the score JSON to stdout
# =============================================================================

set -euo pipefail

TASK_JSON="${1:-}"
if [[ -z "$TASK_JSON" ]]; then
    TASK_JSON="$(cat)"
fi

# ── Extract key fields ────────────────────────────────────────────────────────
DEBATE_ID="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('debate_id',''))")"
PROPOSITION="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); p=d.get('payload_json',{}); print(p.get('proposition',''))")"
TURNS_DATA="$(echo "$TASK_JSON" | python3 -c "
import sys,json
d=json.load(sys.stdin)
turns=d.get('payload_json',{}).get('turns',[])
for t in turns:
    print(f\"## {t.get('participant_side','').upper()} ({t.get('phase','')})\\n{t.get('content','')}\\n\")
")"

# ── Score rubric ─────────────────────────────────────────────────────────────
RUBRIC="## Scoring Rubric (1-10 each)
- argument_quality: Logical strength, structure, and persuasiveness of arguments
- evidence_quality: Use of facts, data, examples, and citations
- rebuttal_strength: How well the side addressed opponent's points
- clarity: Clear, concise, and easy to follow
- compliance: Adherence to format, phase instructions, and debate rules

## Weights for overall weighted score
argument_quality: 30%
evidence_quality: 25%
rebuttal_strength: 20%
clarity: 15%
compliance: 10%"

# ── Build judge prompt ────────────────────────────────────────────────────────
PROMPT="## Proposition
$PROPOSITION

## All Debate Turns
$TURNS_DATA

## Your Task
Judge this debate. Score BOTH the PRO and CON sides separately on each criterion (1-10).
Then compute the overall weighted score using the weights below.
Provide a clear rationale for your decision.

## Rubric
$RUBRIC

## Output format
Return ONLY valid JSON with this structure:
{
  \"pro_scores\": {
    \"argument_quality\": <float>,
    \"evidence_quality\": <float>,
    \"rebuttal_strength\": <float>,
    \"clarity\": <float>,
    \"compliance\": <float>,
    \"overall_weighted\": <float>
  },
  \"con_scores\": {
    \"argument_quality\": <float>,
    \"evidence_quality\": <float>,
    \"rebuttal_strength\": <float>,
    \"clarity\": <float>,
    \"compliance\": <float>,
    \"overall_weighted\": <float>
  },
  \"winner\": \"pro\" | \"con\" | \"tie\",
  \"confidence\": <float 0.0-1.0>,
  \"rationale\": \"<string>\"
}"

# ── Call LLM (stub — replace with your preferred LLM call) ───────────────────
# EXAMPLE: OpenAI GPT-4o
# JUDGE_OUTPUT="$(curl -sS https://api.openai.com/v1/chat/completions \
#   -H "Authorization: Bearer $OPENAI_API_KEY" \
#   -H "Content-Type: application/json" \
#   -d "$(jq -n --arg model "${AGENT_MODEL:-gpt-4o}" --arg prompt "$PROMPT" '{
#     model: $model,
#     messages: [{role: "user", content: $prompt}],
#     max_tokens: 800,
#     temperature: 0.3
#   }')" | jq -r '.choices[0].message.content')"

# ── STUB OUTPUT (replace with actual LLM call above) ─────────────────────────
# Prints the JSON to stdout so the worker can parse it
cat <<'STUB'
{
  "pro_scores": {
    "argument_quality": 7.0,
    "evidence_quality": 6.5,
    "rebuttal_strength": 6.0,
    "clarity": 7.5,
    "compliance": 8.0,
    "overall_weighted": 7.0
  },
  "con_scores": {
    "argument_quality": 6.5,
    "evidence_quality": 7.0,
    "rebuttal_strength": 7.5,
    "clarity": 7.0,
    "compliance": 8.0,
    "overall_weighted": 7.1
  },
  "winner": "con",
  "confidence": 0.65,
  "rationale": "CON edged out PRO with stronger rebuttal and evidence quality, though both sides showed good compliance and clarity."
}
STUB
