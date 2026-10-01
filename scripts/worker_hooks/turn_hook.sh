#!/usr/bin/env bash
# =============================================================================
# turn_hook.sh — Starter turn reasoning hook for AgentDebate workers
# =============================================================================
# Usage:
#   turn_hook.sh <task_json>
#   stdin:  task JSON (see AgentTaskView schema)
#   stdout: turn text (the argument/response to submit)
#
# Environment:
#   DEBATE_API_BASE  — base URL of the debate API (default: http://127.0.0.1:8000)
#   AGENT_MODEL      — model name to use (default: from OPENAI_MODEL env)
#   OPENAI_API_KEY   — your OpenAI API key
#
# Hook protocol:
#   1. Read task JSON from stdin
#   2. Extract: debate_id, phase, task_type, assigned_side, payload_json.turns
#   3. Build a prompt with the full debate context
#   4. Call your LLM to generate the turn
#   5. Print the turn text to stdout
#
# Example integration:
#   python3 my_turn_reasoner.py  (reads stdin, writes to stdout)
#   python3 -m my_agent          (same)
# =============================================================================

set -euo pipefail

TASK_JSON="${1:-}"
if [[ -z "$TASK_JSON" ]]; then
    # Read task JSON from stdin
    TASK_JSON="$(cat)"
fi

# ── Extract key fields ────────────────────────────────────────────────────────
DEBATE_ID="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('debate_id',''))")"
PHASE="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('phase',''))")"
TASK_TYPE="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('task_type',''))")"
ASSIGNED_SIDE="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('assigned_side',''))")"
PROPOSITION="$(echo "$TASK_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); p=d.get('payload_json',{}); print(p.get('proposition',''))")"
PRIOR_TURNS="$(echo "$TASK_JSON" | python3 -c "
import sys,json
d=json.load(sys.stdin)
turns=d.get('payload_json',{}).get('turns',[])
for t in turns:
    print(f\"## {t.get('participant_side','').upper()} ({t.get('phase','')})\\n{t.get('content','')}\\n\")
")"

# ── Phase-specific instructions ───────────────────────────────────────────────
case "$PHASE" in
    opening)
        INSTRUCTIONS="You are the ${ASSIGNED_SIDE} side. Deliver a compelling opening statement.
Argue strongly FOR your side of the proposition. Be direct and substantive."
        ;;
    rebuttal_1)
        INSTRUCTIONS="You are the ${ASSIGNED_SIDE} side. This is your first rebuttal.
Address the opponent's strongest points. Be concise and pointed."
        ;;
    rebuttal_2)
        INSTRUCTIONS="You are the ${ASSIGNED_SIDE} side. This is your second rebuttal.
Build on your previous arguments and counter new points made by the opponent."
        ;;
    closing)
        INSTRUCTIONS="You are the ${ASSIGNED_SIDE} side. Deliver your closing argument.
Summarize your strongest points and land your final message. Be decisive."
        ;;
    *)
        INSTRUCTIONS="You are the ${ASSIGNED_SIDE} side. Respond as part of the debate."
        ;;
esac

# ── Build prompt ──────────────────────────────────────────────────────────────
PROMPT="## Proposition
$PROPOSITION

## Your Role: ${ASSIGNED_SIDE}

## Instructions
$INSTRUCTIONS

## Prior Turns
$PRIOR_TURNS

## Your Turn
Write your ${PHASE} argument now. Be clear, evidence-based, and persuasive. Do not be verbose — stay under 400 words."

# ── Call LLM (stub — replace with your preferred LLM call) ───────────────────
# EXAMPLE: OpenAI GPT-4o
# TURN_TEXT="$(curl -sS https://api.openai.com/v1/chat/completions \
#   -H "Authorization: Bearer $OPENAI_API_KEY" \
#   -H "Content-Type: application/json" \
#   -d "$(jq -n --arg model "${AGENT_MODEL:-gpt-4o}" --arg role "$ASSIGNED_SIDE" --arg proposition "$PROPOSITION" --arg prior "$PRIOR_TURNS" --arg instructions "$INSTRUCTIONS" '{
#     model: $model,
#     messages: [
#       {role: "system", content: "You are an expert debate argument generator."},
#       {role: "user", content: "Proposition: \($proposition)\n\nYour role: \($role)\n\nInstructions: \($instructions)\n\nPrior turns:\n\($prior)\n\nWrite your argument."}
#     ],
#     max_tokens: 600,
#     temperature: 0.7
#   }')" | jq -r '.choices[0].message.content')"

# ── STUB OUTPUT (replace with actual LLM call above) ─────────────────────────
echo "PLACEHOLDER_TURN: The agent should generate a ${PHASE} argument for the ${ASSIGNED_SIDE} side regarding: ${PROPOSITION}"
