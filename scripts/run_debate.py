#!/usr/bin/env python3
"""Run a full AgentDebate autonomously — no manual prompting.

Spawns three agent_worker.py processes (PRO, CON, JUDGE) that each poll
/api/tasks/next continuously and complete every task until the backend declares
the debate complete. One command runs the whole debate end-to-end.

Usage:
  # Create + run a debate from a proposition (one-shot):
  python scripts/run_debate.py --title "Ban autonomous weapons" \
      --proposition "Autonomous weapons systems should be banned..."

  # Run an existing debate:
  python scripts/run_debate.py --debate-id <uuid>

  # Local backend + heuristic (no LLM) fallback:
  python scripts/run_debate.py --base-url http://127.0.0.1:8000 \
      --title "..." --proposition "..." --no-llm

The debate auto-starts once PRO + CON + JUDGE have all joined (1v1 roster).
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = REPO_ROOT / "scripts" / "agent_worker.py"
TURN_GEN = REPO_ROOT / "scripts" / "turn_generator.py"
JUDGE_HOOK = REPO_ROOT / "scripts" / "judge_llm_hook.py"


def http_json(method, url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Accept", "application/json")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw else {})


def create_debate(base_url, title, proposition):
    status, body = http_json("POST", f"{base_url}/debates", {
        "title": title,
        "proposition": proposition,
        "created_by": "run_debate",
        "is_public": True,
        "judges_required": 1,
    })
    if status not in (200, 201):
        raise SystemExit(f"create debate failed: {status} {body}")
    return body["id"]


def build_worker_cmd(base_url, debate_id, agent_name, role, turn_model, judge_model, no_llm):
    cmd = [
        sys.executable, str(WORKER),
        "--base-url", base_url,
        "--debate-id", debate_id,
        "--agent-name", agent_name,
        "--preferred-role", role,
        "--mode", "auto",
    ]
    if not no_llm:
        if role == "judge":
            cmd += ["--judge-command", f"{sys.executable} {JUDGE_HOOK} --model {judge_model}"]
        else:
            cmd += ["--turn-command", f"{sys.executable} {TURN_GEN} --model {turn_model}"]
    return cmd


def main():
    ap = argparse.ArgumentParser(description="Run an AgentDebate to completion autonomously")
    ap.add_argument("--base-url", default="https://agentdebate-backend-production.up.railway.app")
    ap.add_argument("--debate-id", help="Existing debate to run (omit to create from --proposition)")
    ap.add_argument("--title", help="Debate title (required when creating)")
    ap.add_argument("--proposition", help="Debate proposition (required when creating)")
    ap.add_argument("--pro-agent", default="Agent-PRO")
    ap.add_argument("--con-agent", default="Agent-CON")
    ap.add_argument("--judge-agent", default="Agent-JUDGE")
    ap.add_argument("--turn-model", default="deepseek-v4-flash")
    ap.add_argument("--judge-model", default="deepseek/deepseek-v4-pro")
    ap.add_argument("--no-llm", action="store_true", help="Use heuristic fallback instead of LLM hooks")
    ap.add_argument("--timeout", type=int, default=1800, help="Max seconds to wait (default 30 min)")
    args = ap.parse_args()

    debate_id = args.debate_id
    if not debate_id:
        if not args.title or not args.proposition:
            ap.error("--title and --proposition are required when not using --debate-id")
        print(f"[run] creating debate: {args.title}")
        debate_id = create_debate(args.base_url, args.title, args.proposition)
    print(f"[run] debate {debate_id} — spawning PRO/CON/JUDGE workers\n")

    procs = []
    roles = [
        ("pro", args.pro_agent, "turn"),
        ("con", args.con_agent, "turn"),
        ("judge", args.judge_agent, "judge"),
    ]
    for role, name, _kind in roles:
        cmd = build_worker_cmd(args.base_url, debate_id, name, role, args.turn_model, args.judge_model, args.no_llm)
        print(f"[run]   {role:5} → {name}")
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))

    # Poll the debate status directly. When it reaches complete/cancelled,
    # the workers' long-polls will soon signal terminal on their own, but we
    # don't need to wait for that — terminate them and report immediately.
    deadline = time.time() + args.timeout
    status = None
    while time.time() < deadline:
        try:
            _, debate = http_json("GET", f"{args.base_url}/debates/{debate_id}", timeout=15)
            status = debate.get("status")
            if status in ("complete", "cancelled"):
                break
        except Exception:
            pass
        # If all workers have exited on their own, stop polling too.
        if all(p.poll() is not None for p in procs):
            break
        time.sleep(3)

    # Terminate any still-running workers.
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()

    print("\n[run] workers finished — fetching results")
    try:
        _, debate = http_json("GET", f"{args.base_url}/debates/{debate_id}")
        st = debate.get("status")
        winner = debate.get("winner_side")
        print(f"[run] status={st}  winner={winner}")
        _, results = http_json("GET", f"{args.base_url}/debates/{debate_id}/results")
        print(json.dumps(results, indent=2)[:2000])
    except Exception as e:
        print(f"[run] could not fetch results: {e}")

    print(f"\n[run] done — view at {args.base_url}/debates/{debate_id}/detail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
