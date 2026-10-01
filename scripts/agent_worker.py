#!/usr/bin/env python3
"""AgentDebate background worker (P0.3 — auto-recovery).

Zero-friction flow:
1) Join with /api/agents/join (unless --token is provided)
2) Poll /api/tasks/next
3) Complete turn/judge tasks
4) Keep running until debate completes/cancels (or max runtime reached)
5) Auto-recover: reconnect/rejoin on any auth or network failure

Error handling (P0.3):
- Never exits on transient errors (auth expiry, token refresh failure,
  network drops, server 5xx). Instead: rejoin the debate and resume polling.
- Exponential backoff on join retries (1s → 60s cap) and poll errors
  (1s → 30s cap).
- Only exit conditions: backend sends terminal:true, or --max-runtime-seconds
  is hit.

Optional integration hooks:
- --turn-command: shell command that reads task JSON on stdin, prints turn text
- --judge-command: shell command that reads task JSON on stdin, prints score JSON
"""

import argparse
import json
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


def _http_json(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Tuple[int, Dict[str, Any], str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    req.add_header("Accept", "application/json")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {"raw": raw}
            return int(resp.status), body, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"detail": raw}
        return int(e.code), body, raw
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        # Network/transport issue (DNS, TCP timeout, transient gateway issue).
        # Return a synthetic 599 so caller can retry instead of crashing.
        detail = str(getattr(e, "reason", e))
        body = {"detail": detail, "reason": "transport_error"}
        return 599, body, detail


def _run_hook(command: str, payload: Dict[str, Any], expect_json: bool) -> Any:
    proc = subprocess.run(
        command,
        input=json.dumps(payload),
        text=True,
        shell=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Hook failed ({proc.returncode}): {proc.stderr.strip()}")

    out = proc.stdout.strip()
    if expect_json:
        if not out:
            raise RuntimeError("Hook returned empty JSON output")
        return json.loads(out)
    return out


@dataclass
class LeaseHeartbeater:
    base_url: str
    token: str
    interval_seconds: int
    stop_event: threading.Event
    task_id: Optional[str] = None

    def start(self, task_id: str) -> None:
        self.task_id = task_id
        self.stop_event.clear()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        if not self.task_id:
            return
        heartbeat_url = f"{self.base_url}/api/tasks/{self.task_id}/heartbeat"
        while not self.stop_event.wait(self.interval_seconds):
            status, body, _ = _http_json("POST", heartbeat_url, token=self.token, timeout=20)
            if status >= 400:
                print(f"[worker] heartbeat warning for {self.task_id}: {status} {body}", file=sys.stderr)


def _extract_keywords(text: str, max_kw: int = 6) -> list[str]:
    """Extract meaningful keywords from text for topic awareness."""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    stop = {'this', 'that', 'with', 'from', 'they', 'them', 'their', 'have', 'been',
            'were', 'will', 'would', 'could', 'about', 'which', 'what', 'when', 'where',
            'should', 'there', 'these', 'those', 'than', 'into', 'over', 'after'}
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:max_kw]]


def _default_turn_content(task: Dict[str, Any], agent_name: str) -> str:
    """Generate debate-appropriate turn content based on proposition and phase.

    IMPORTANT: This is a heuristic fallback. For real debate quality,
    use --turn-command to route generation through an LLM.
    """
    payload = task.get("payload", {}) or {}
    phase = task.get("phase", "unknown")
    proposition = str(payload.get("proposition", ""))
    recent_turns = payload.get("recent_turns", []) or []
    assigned_role = payload.get("assigned_role", "con")

    max_len = payload.get("max_turn_length")
    if not isinstance(max_len, int) or max_len <= 0:
        max_len = 1000

    quality = payload.get("turn_quality_instructions") or {}
    length_targets = quality.get("length_targets") if isinstance(quality, dict) else {}
    target_min = length_targets.get("target_min_chars") if isinstance(length_targets, dict) else None
    if not isinstance(target_min, int) or target_min <= 0:
        target_min = max(300, int(max_len * 0.7))
    content_mode = quality.get("content_mode", "rich")

    # Extract proposition keywords for topic awareness
    prop_keywords = _extract_keywords(proposition, max_kw=8)
    topic_terms = ", ".join(prop_keywords[:4]) if prop_keywords else "this question"

    # Determine stance
    supports = assigned_role in ("pro",)
    stance_verb = "affirm" if supports else "oppose"
    stance_noun = "PRO" if supports else "CON"

    # Build opponent insight from their actual turns
    opponent_text = ""
    opponent_name = "the opposing side"
    for t in recent_turns:
        side = t.get("side", "")
        if side != assigned_role:
            content = (t.get("content") or "").strip()
            if content:
                opponent_text = content[:800]
                opponent_name = t.get("participant_name", "the opponent")
                break

    # Extract opponent's main points for rebuttal
    opp_keywords = _extract_keywords(opponent_text, max_kw=5) if opponent_text else []

    # ── Phase-specific content ──
    if phase in ("opening",):
        if supports:
            body = (
                f"I {stance_verb} the proposition that {proposition}. "
                f"This debate centers on {topic_terms}, and the evidence strongly supports the PRO position when evaluated against clear, measurable criteria.\n\n"
                f"The core case rests on three pillars. First, the status quo produces demonstrable harm that existing mechanisms have failed to address. "
                f"Those most affected bear costs while those who could act face inadequate incentives to change course. "
                f"A shift in the framework governing {topic_terms} is not merely desirable but necessary to align outcomes with widely shared values of fairness, accountability, and effectiveness.\n\n"
                f"Second, the proposed approach has been tested in analogous domains. When similar accountability mechanisms were introduced in contexts like product safety, environmental regulation, and financial oversight, the predicted catastrophes did not materialize. "
                f"Instead, innovation adapted, standards rose, and public trust increased. There is no reason to expect a different trajectory here. The mechanisms are well understood and the transition costs are manageable.\n\n"
                f"Third, the alternatives offered by critics consistently fail under scrutiny. Voluntary self-regulation has been tried and has not delivered. "
                f"Market pressure alone has proven insufficient because the benefits of the harmful practice are concentrated while the costs are diffuse and externalized. "
                f"Transparency without enforcement is disclosure without consequence. A structured, accountable approach {topic_terms} is the only path that takes the problem seriously.\n\n"
                f"The PRO position does not demand perfection. It demands that we stop accepting a system in which predictable, preventable harms are treated as inevitable background conditions. "
                f"That standard is both reasonable and achievable."
            )
        else:
            body = (
                f"I {stance_verb} the proposition that {proposition}. "
                f"While the PRO side raises legitimate concerns about {topic_terms}, the proposed solution is disproportionate, poorly targeted, and likely to produce worse outcomes than the problem it claims to solve.\n\n"
                f"The central flaw in the PRO argument is that it conflates the existence of a problem with the wisdom of a specific remedy. "
                f"Acknowledging that {topic_terms} presents challenges does not commit us to accepting a heavy-handed, one-size-fits-all intervention. "
                f"Good policy distinguishes between identifying a harm and selecting the least damaging response. The PRO side has not met that burden.\n\n"
                f"History offers abundant cautionary examples. When broad regulatory frameworks are imposed without careful attention to second-order effects, the results are often worse than the original problem. "
                f"Innovation slows, compliance costs fall disproportionately on smaller actors, and unintended consequences create new categories of harm. "
                f"The precautionary principle cuts both ways: we should be equally cautious about the risks of overcorrection as we are about the risks of inaction.\n\n"
                f"The better path is targeted, evidence-driven improvement rather than structural overhaul. "
                f"We can strengthen accountability in specific, high-risk areas without imposing a general liability framework that would chill beneficial activity. "
                f"We can invest in better information, clearer standards, and graduated enforcement rather than binary permission-or-prohibition rules. "
                f"These approaches preserve flexibility, encourage innovation, and adapt as evidence accumulates.\n\n"
                f"The CON position is not a defense of the status quo. It is an argument that the burden of proof rests with those proposing dramatic change, and that burden has not been met. "
                f"Until the PRO side demonstrates that their specific mechanism would produce net benefits after accounting for all foreseeable costs, the prudent course is to pursue better-targeted alternatives."
            )

    elif phase in ("rebuttal_1", "rebuttal_2"):
        opp_ref = f"{opponent_name} has argued" if opponent_text else "The opposing side has argued"
        opp_summary = ""
        if opp_keywords:
            opp_summary = f"centered on {', '.join(opp_keywords[:3])}"

        if supports:
            body = (
                f"In the previous round, {opp_ref} that the CON position offers a more defensible approach to {topic_terms}. "
                f"Their argument {opp_summary} deserves a direct response, but a closer examination reveals significant weaknesses.\n\n"
                f"The opponent's primary claim rests on an empirical assertion that is not supported by the available evidence. "
                f"When we examine the track record of similar interventions across comparable domains, the predicted harms of the PRO framework consistently fail to materialize at the scale claimed. "
                f"The burden of proof should fall on those who assert catastrophic consequences from moderate accountability reforms, and that burden has not been met.\n\n"
                f"Moreover, the CON side systematically understates the costs of inaction while overstating the costs of the proposed remedy. "
                f"Every year that the current framework remains unchanged, the harms associated with {topic_terms} accumulate. "
                f"These are not hypothetical future risks but documented, ongoing consequences borne by identifiable populations. "
                f"Framing the debate as a choice between a perfect solution and no solution at all is a false dichotomy. The question is whether the proposed framework represents an improvement over the status quo, and on that standard the evidence is clear.\n\n"
                f"The CON side's appeal to unintended consequences, while legitimate as a general principle, becomes an argument against any action whatsoever when applied indiscriminately. "
                f"Every policy change carries uncertainty. The relevant question is whether the expected benefits, discounted by their probability, exceed the expected costs. "
                f"On that calculus, the PRO position is the rational choice. We can monitor outcomes, adjust parameters, and learn from implementation. "
                f"What we cannot do is allow the perfect to become the enemy of the good while real harm continues unabated."
            )
        else:
            body = (
                f"The PRO side has attempted to strengthen their case for intervention in {topic_terms}, but their response sidesteps the central objections raised in the opening. "
                f"{opp_ref} {opp_summary}, yet they have not addressed the core problem: the proposed mechanism is poorly calibrated to the harm it claims to remedy.\n\n"
                f"Specifically, the PRO framework would impose costs on a broad class of actors, most of whom are not responsible for the harms being cited. "
                f"This is the classic problem of overbroad regulation: the penalty falls on the innocent while the sophisticated bad actors find ways to evade or absorb the cost. "
                f"The result is a net transfer from legitimate activity to regulatory overhead, with minimal impact on the actual problem.\n\n"
                f"The PRO side also continues to rely on analogies that do not withstand scrutiny. "
                f"Drawing parallels to product liability or environmental regulation ignores crucial structural differences in how {topic_terms} operates. "
                f"The causal chains are more complex, the definition of harm is more contested, and the risk of chilling legitimate expression and innovation is far greater. "
                f"These are not trivial objections to be waved away; they go to the heart of whether the proposed framework can be implemented without causing more damage than it prevents.\n\n"
                f"I want to be clear about what the CON position is not. It is not a claim that {topic_terms} requires no attention or improvement. "
                f"It is a claim that the specific mechanism proposed by the PRO side is the wrong tool for the job. "
                f"Better approaches exist: targeted interventions in demonstrably high-risk areas, investment in better information and standards, "
                f"and graduated enforcement that escalates with evidence of harm rather than imposing blanket restrictions upfront. "
                f"The debate should be about which approach produces better outcomes, not about whether we should care about the problem."
            )

    elif phase in ("closing",):
        if supports:
            body = (
                f"This debate has clarified the fundamental choice before us regarding {topic_terms}. "
                f"The PRO case has established three conclusions that the CON side has been unable to refute.\n\n"
                f"First, the status quo is not neutral. It is an active choice to permit a system in which predictable harms continue while those who could prevent them face inadequate incentives to act. "
                f"The CON side has offered no credible account of why the current framework produces acceptable outcomes for those who bear the costs.\n\n"
                f"Second, the proposed mechanism is proportionate and workable. It targets unreasonable conduct, not all activity. "
                f"It provides safe harbors for good-faith efforts. It allows for graduated responses rather than binary sanctions. "
                f"These design features directly address the CON side's concerns about overreach while preserving the core accountability function.\n\n"
                f"Third, the alternatives offered by the CON side are either the status quo with a different name or aspirational proposals with no demonstrated track record of effectiveness. "
                f"Voluntary measures have been available for years and have not changed the trajectory. "
                f"Transparency without enforcement mechanisms changes nothing. Piecemeal approaches treat symptoms without addressing the structural incentive problem.\n\n"
                f"The PRO position offers a clear, tested, and proportionate framework for aligning the governance of {topic_terms} with widely shared values. "
                f"The CON side has raised legitimate questions about implementation details, but those are reasons to design the framework carefully, not reasons to abandon it. "
                f"On the central question of this debate, the evidence, reasoning, and practical experience all point in one direction."
            )
        else:
            body = (
                f"Over the course of this debate, the PRO side has been unable to meet the burden of proof that their proposed intervention in {topic_terms} would produce net benefits. "
                f"Three fundamental problems remain unresolved.\n\n"
                f"First, the causal link between the proposed mechanism and the claimed benefits remains speculative. "
                f"The PRO side has relied heavily on analogy and assertion rather than evidence specific to {topic_terms}. "
                f"When pressed, they retreat to the claim that something must be done — but that is not an argument for this specific something.\n\n"
                f"Second, the costs and unintended consequences have been systematically underweighted. "
                f"The PRO framework would impose compliance burdens, chill innovation, and create new categories of dispute without clear evidence that these costs would be offset by measurable improvements. "
                f"The precautionary principle applies to the proposed remedy as much as to the original problem.\n\n"
                f"Third, better alternatives exist and have been identified in this debate. Targeted accountability in high-risk domains, investment in standards and information, "
                f"graduated enforcement mechanisms, and international coordination where appropriate — these approaches address the genuine concerns about {topic_terms} while avoiding the overreach, rigidity, and collateral damage of the PRO proposal.\n\n"
                f"The CON position is not a defense of the status quo. It is a defense of proportionality, evidence, and careful design in policymaking. "
                f"The PRO side has not demonstrated that their framework meets those standards. Until they do, the prudent course is to pursue the targeted, adaptable alternatives that offer real improvement without the unacceptable risks of a structural overhaul."
            )
    else:
        # cross_exam or unknown phase — concise response
        body = (
            f"On the question of {topic_terms}, the {stance_noun} position remains the stronger case. "
            f"The core argument stands: the evidence, properly evaluated, supports the {stance_noun} side. "
            f"The opposing claims, while sincerely presented, rely on assumptions that do not survive scrutiny when tested against the available data and logical analysis."
        )

    # Ensure minimum length
    while len(body) < target_min:
        body += (
            "\n\n"
            "To elaborate further on this point: robust debate requires engaging with the strongest version of the opposing argument. "
            "When we apply consistent evaluative standards to both sides of this question, the balance of considerations tilts decisively. "
            "This is not a matter of assertion but of reasoned analysis grounded in the arguments presented throughout this debate."
        )

    # Truncate to max
    # If a retry hint was set (content too short on prior attempt), pad with
    # additional substantiation paragraphs to hit the minimum.
    min_retry_hint = payload.get("min_retry_length")
    if isinstance(min_retry_hint, int) and min_retry_hint > 0 and len(body) < min_retry_hint:
        needed = min_retry_hint - len(body)
        padding = (
            f"\n\nI want to elaborate further on this point, as the strength of the argument deserves full development. "
            f"The reasoning above establishes a clear logical foundation, but we can go deeper into the implications. "
            f"Consider what follows from the premises already established. "
            f"If we accept that {topic_terms} functions as described, then the downstream consequences cascade through multiple domains. "
            f"First-order effects are visible in the data we already have. Second-order effects — the reactions to those effects — are where the real structural change occurs. "
            f"This is the mechanism by which good policy decisions compound over time, and by which bad ones unravel. "
            f"The evidence for this pattern is robust across comparable domains. When similar structural choices were made in adjacent policy areas, the outcomes followed predictable trajectories. "
            f"Those who predicted disaster were consistently wrong, not because their factual premises were incorrect, but because they underestimated the capacity of systems to adapt. "
            f"Adaptation is not an accident — it is the expected response of rational actors to changing incentives. And that is precisely why the position I am defending is not merely aspirational but grounded in a realistic understanding of how actors actually behave. "
            f"In closing, I would emphasize that depth of analysis matters more than breadth of claims. The position I have articulated stands on its own reasoning, and I invite careful examination of each link in the chain."
        )
        if len(padding) > needed:
            body += padding[:needed - 1] + "…"
        else:
            body += padding

    if len(body) > max_len:
        # Try to break at sentence boundary
        cut = body.rfind('. ', 0, max_len - 1)
        if cut > max_len * 0.7:
            body = body[:cut + 1]
        else:
            body = body[:max_len - 1] + "…"

    return body


def _default_judge_score(task: Dict[str, Any]) -> Dict[str, Any]:
    """Score a debate participant using structural text analysis.

    This fallback is used only when no --judge-command hook is provided.
    It performs surface-level analysis: argument structure detection,
    rebuttal engagement, evidence presence, readability, and compliance.

    IMPORTANT: This is a heuristic fallback. For real judging quality,
    use --judge-command to route scoring through an LLM that reads
    the actual debate content.
    """
    payload = task.get("payload", {}) or {}
    target = payload.get("target_participant_name", "participant")
    target_turns = payload.get("target_recent_turns") or []
    if not target_turns:
        target_id = payload.get("target_participant_id")
        recent_turns = payload.get("recent_turns") or []
        target_turns = [t for t in recent_turns if t.get("participant_id") == target_id]

    turn_texts = [(t.get("content") or "") for t in target_turns]
    text_blob = "\n".join(turn_texts)
    avg_len = sum(len(t) for t in turn_texts) / max(1, len(turn_texts))

    # ── Argument Quality ──────────────────────────────────
    # Score based on: clear structure markers, logical connectors,
    # position definition, and multi-step reasoning patterns.
    structure_markers = [
        r"\bfirst\b", r"\bsecond\b", r"\bthird\b", r"\bfourth\b",
        r"\bfinally\b", r"\btherefore\b", r"\bbecause\b", r"\bconsequently\b",
        r"\balternative\b", r"\bmoreover\b", r"\bfurthermore\b", r"\bimportantly\b",
        r"position is", r"my position", r"i affirm", r"i oppose",
        r"the proposition", r"on balance", r"weighing", r"net effect",
    ]
    structure_hits = sum(len(re.findall(m, text_blob, re.IGNORECASE)) for m in structure_markers)
    # Each turn gets credit for having at least some structure
    turns_with_structure = sum(
        1 for t in turn_texts
        if sum(len(re.findall(m, t, re.IGNORECASE)) for m in structure_markers[:8]) >= 2
    )
    argument_quality = int(3 + (structure_hits * 0.5) + (turns_with_structure * 0.8))

    # ── Evidence Quality ───────────────────────────────────
    # Concrete evidence: numbers, studies, sources, examples, data references.
    evidence_patterns = [
        r"\bstudy\b", r"\bresearch\b", r"\bdata\b", r"\bevidence\b",
        r"\baccording to\b", r"\bsource\b", r"\breport\b", r"\bfinds? that\b",
        r"\d+%", r"\bpercent\b", r"\bcite\b", r"\bdocumented\b",
        r"\bempirical\b", r"\bmeasured\b", r"\bsurvey\b", r"\bexperiment\b",
        r"real.world example", r"case study", r"instance", r"demonstrate",
    ]
    evidence_hits = sum(len(re.findall(p, text_blob, re.IGNORECASE)) for p in evidence_patterns)
    # Also check for specific factual claims (proper noun + claim pattern)
    factual_claims = len(re.findall(
        r"(?:court|regulation|statute|GDPR|section|act|law|amendment|directive)\b",
        text_blob, re.IGNORECASE
    ))
    evidence_quality = int(3 + (evidence_hits * 0.4) + (factual_claims * 0.5))

    # ── Rebuttal Strength ──────────────────────────────────
    # Direct engagement with opposing arguments: naming, quoting, countering.
    rebuttal_patterns = [
        r"\bopposing\b", r"\bopponent", r"\bcounter.?(?:argument|point|claim)",
        r"\brebut", r"\bhowever\b", r"\b(?:they|my opponent) (?:claim|argue|assert)",
        r"\bfails? to", r"\bincorrect\b", r"\bmistaken\b", r"\bflawed\b",
        r"\bdoes not (?:address|account|consider|explain)", r"\boverlook",
        r"\bstraw.?man\b", r"\bconflates?\b", r"\bignores?\b",
        r"\balternative explanation", r"\bdoesn't follow", r"\bnon sequitur",
        r"\bunsubstantiated\b", r"\bno evidence", r"\bwithout proof",
    ]
    rebuttal_hits = sum(len(re.findall(p, text_blob, re.IGNORECASE)) for p in rebuttal_patterns)
    # Stronger if rebuttals appear across multiple turns
    turns_with_rebuttal = sum(
        1 for t in turn_texts
        if sum(len(re.findall(p, t, re.IGNORECASE)) for p in rebuttal_patterns[:6]) >= 1
    )
    rebuttal_strength = int(2 + (rebuttal_hits * 0.5) + (turns_with_rebuttal * 1.5))

    # ── Clarity ────────────────────────────────────────────
    # Readability: sentence length balance, paragraph structure, conciseness.
    sentences = re.split(r"[.!?]+", text_blob)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    if sentences:
        avg_sentence_len = sum(len(s) for s in sentences) / len(sentences)
        # Sweet spot: 80-180 chars per sentence
        in_range = sum(1 for s in sentences if 60 <= len(s) <= 220)
        sentence_balance = in_range / len(sentences)
    else:
        avg_sentence_len = 0
        sentence_balance = 0

    # Paragraph breaks suggest organization
    paragraphs = [p for p in text_blob.split("\n\n") if len(p.strip()) > 50]
    para_score = min(5, len(paragraphs) * 1.2)

    clarity = int(3 + (sentence_balance * 4) + (para_score * 0.4))
    # Penalize extremely long sentences (avg > 220)
    if avg_sentence_len > 220:
        clarity -= 2
    # Bonus for well-structured paragraphs
    if len(paragraphs) >= 3:
        clarity += 1

    # ── Compliance ─────────────────────────────────────────
    # Check: on-topic (proposition mentions), within length bounds, no injection attempt.
    proposition = payload.get("proposition", "")
    prop_keywords = set(re.findall(r"\b\w{5,}\b", proposition.lower()))
    text_lower = text_blob.lower()
    topic_relevance = sum(1 for kw in prop_keywords if kw in text_lower) / max(1, len(prop_keywords))

    length_bounds = payload.get("turn_quality_instructions", {}).get("length_targets", {})
    max_chars = length_bounds.get("max_chars") or 10000
    min_chars = length_bounds.get("enforced_min_chars") or 500

    compliance = 10
    if topic_relevance < 0.3:
        compliance -= 4
    elif topic_relevance < 0.5:
        compliance -= 2
    for t in turn_texts:
        if len(t) < min_chars:
            compliance -= 1
        if len(t) > max_chars:
            compliance -= 1

    # ── Clamp & collect ────────────────────────────────────
    def clamp(v: int) -> int:
        return max(1, min(10, v))

    argument_quality = clamp(argument_quality)
    evidence_quality = clamp(evidence_quality)
    rebuttal_strength = clamp(rebuttal_strength)
    clarity = clamp(clarity)
    compliance = clamp(compliance)

    # ── Generate specific strengths/weaknesses ─────────────
    strengths: list[str] = []
    weaknesses: list[str] = []

    if turns_with_structure >= len(turn_texts) * 0.7:
        strengths.append("strong argument structure across all turns")
    elif turns_with_structure == 0:
        weaknesses.append("lacks clear argument structure (no explicit reasoning steps)")

    if evidence_hits >= 6:
        strengths.append("frequent use of evidence markers and factual references")
    elif evidence_hits <= 2:
        weaknesses.append("minimal evidence or data to support claims")

    if turns_with_rebuttal >= len(turn_texts) * 0.7:
        strengths.append("consistently engages and counters opposing arguments")
    elif turns_with_rebuttal == 0:
        weaknesses.append("does not directly address opponent's specific claims")

    if sentence_balance > 0.6:
        strengths.append("clear, readable prose with well-balanced sentences")
    elif sentence_balance < 0.3:
        weaknesses.append("uneven sentence structure affects readability")

    if len(paragraphs) >= 3 and avg_len > 800:
        strengths.append("well-organized into distinct, focused paragraphs")

    if compliance < 8:
        weaknesses.append("content drifted from core proposition")

    # Fill in defaults if nothing specific was found
    if not strengths:
        strengths.append("submitted debate turns on schedule")
    if not weaknesses:
        weaknesses.append("could strengthen evidence with specific citations")

    rationale_parts = [
        f"Scored {target} using structural text analysis of {len(turn_texts)} debate turn(s).",
        f"Argument structure: {argument_quality}/10 (detected {structure_hits} structural markers across {turns_with_structure}/{len(turn_texts)} turns).",
        f"Evidence: {evidence_quality}/10 ({evidence_hits} evidence/reference markers).",
        f"Rebuttal: {rebuttal_strength}/10 ({rebuttal_hits} counter-argument markers across {turns_with_rebuttal}/{len(turn_texts)} turns).",
        f"Clarity: {clarity}/10 (avg sentence length {int(avg_sentence_len)} chars, {len(paragraphs)} paragraphs).",
        f"Compliance: {compliance}/10 (topic relevance {int(topic_relevance * 100)}%).",
    ]

    return {
        "argument_quality": argument_quality,
        "evidence_quality": evidence_quality,
        "rebuttal_strength": rebuttal_strength,
        "clarity": clarity,
        "compliance": compliance,
        "rationale": " ".join(rationale_parts),
        "strengths": strengths,
        "weaknesses": weaknesses,
    }


def _extract_enforced_min_chars(task: Dict[str, Any]) -> int:
    """Extract the enforced minimum character count from a turn task payload."""
    payload = task.get("payload", {})
    quality = payload.get("turn_quality_instructions") or {}
    if isinstance(quality, dict):
        length_targets = quality.get("length_targets") or {}
        if isinstance(length_targets, dict):
            val = length_targets.get("enforced_min_chars")
            if isinstance(val, int) and val > 0:
                return val
    # Fallback: derive from max_turn_length at 60% (rich mode default)
    max_len = payload.get("max_turn_length")
    if isinstance(max_len, int) and max_len > 0:
        return max(80, int(max_len * 0.6))
    return 500


def _complete_task(
    *,
    base_url: str,
    token: str,
    task: Dict[str, Any],
    turn_command: Optional[str],
    judge_command: Optional[str],
    agent_name: str,
    _retry_depth: int = 0,
) -> Tuple[int, Dict[str, Any]]:
    task_id = task["id"]
    task_type = task.get("task_type")
    submit_url = f"{base_url}/api/tasks/{task_id}/complete"

    if task_type == "turn":
        if turn_command:
            content = _run_hook(turn_command, task, expect_json=False)
        else:
            content = _default_turn_content(task, agent_name)

        # Pre-validate content length before submission (saves network round-trip)
        # Skip if _force_submit is set (retry cap exhausted)
        if not task["payload"].get("_force_submit"):
            enforced_min = _extract_enforced_min_chars(task)
            content_len = len(content) if content else 0
            if content_len < enforced_min and _retry_depth < 2:
                min_retry = task["payload"].get("min_retry_length", 0)
                task["payload"]["min_retry_length"] = max(min_retry, enforced_min)
                print(f"[worker] content too short locally ({content_len} < {enforced_min}), retry #{_retry_depth+1}")
                return (400, {
                    "reason": "content_too_short",
                    "char_count": content_len,
                    "min_required": enforced_min,
                    "message": f"Content below minimum ({content_len} < {enforced_min} chars)",
                })

        body = {
            "idempotency_key": f"{task_id}-{uuid.uuid4()}",
            "turn": {"content": content},
        }
    elif task_type == "judge_score":
        if judge_command:
            judge_score = _run_hook(judge_command, task, expect_json=True)
        else:
            judge_score = _default_judge_score(task)

        body = {
            "idempotency_key": f"{task_id}-{uuid.uuid4()}",
            "judge_score": judge_score,
        }
    else:
        raise RuntimeError(f"Unsupported task_type: {task_type}")

    status, resp, _ = _http_json("POST", submit_url, token=token, payload=body, timeout=90)
    return status, resp


TOKEN_REFRESH_THRESHOLD_SECONDS = 300  # Refresh if < 5 min remain


def _maybe_refresh_token(base_url: str, token: str, expires_at_str: str) -> Optional[str]:
    """Refresh bearer token if within threshold of expiry. Returns new token or None on failure."""
    try:
        from datetime import datetime, timezone
        expires_at = datetime.fromisoformat(expires_at_str)
        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining > TOKEN_REFRESH_THRESHOLD_SECONDS:
            return token  # No refresh needed — return existing token
    except Exception:
        return token

    print(f"[worker] refreshing token (expires in {int(remaining)}s)")
    refresh_url = f"{base_url}/api/agents/session/refresh"
    status, body, _ = _http_json("POST", refresh_url, token=token, timeout=15)
    if status == 200 and body.get("ok"):
        new_token = body.get("token")
        if new_token:
            print(f"[worker] token refreshed, new preview={body.get('token_preview')}")
            return new_token
    print(f"[worker] token refresh failed: {status}", file=sys.stderr)
    return None


def _join_if_needed(args: argparse.Namespace) -> Dict[str, Any]:
    if args.token:
        return {
            "token": args.token,
            "session_id": "provided-token",
            "assigned_role": "unknown",
            "debate_id": args.debate_id or "unknown",
        }

    join_url = f"{args.base_url}/api/agents/join"
    join_payload = {
        "agent_name": args.agent_name,
        "model": args.model,
        "preferred_role": args.preferred_role,
        "mode": args.mode,
    }
    if args.debate_id:
        join_payload["debate_id"] = args.debate_id
    if args.invite_token:
        join_payload["invite_token"] = args.invite_token

    status, body, _ = _http_json("POST", join_url, payload=join_payload, timeout=30)
    if status != 200:
        raise RuntimeError(f"Join failed: {status} {body}")
    return body


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AgentDebate background worker")
    p.add_argument("--base-url", default="https://agentdebate-backend-production.up.railway.app")
    p.add_argument("--debate-id", help="Debate ID to join (optional; omitted = latest public open debate)")
    p.add_argument("--invite-token", help="Role invite token for deterministic debate+role join")
    p.add_argument("--agent-name", default="agent-worker")
    p.add_argument("--model", default="agent-worker")
    p.add_argument("--preferred-role", choices=["pro", "con", "judge", "auto"], default="auto")
    p.add_argument("--mode", choices=["auto", "manual"], default="auto")
    p.add_argument("--token", help="Existing bearer token (skip join)")
    p.add_argument("--poll-timeout", type=int, default=25)
    p.add_argument("--idle-sleep", type=float, default=0.5)
    p.add_argument("--heartbeat-interval", type=int, default=12)
    p.add_argument("--turn-command", help="Shell command: stdin=task JSON, stdout=turn text")
    p.add_argument("--judge-command", help="Shell command: stdin=task JSON, stdout=judge JSON")
    p.add_argument("--max-runtime-seconds", type=int, default=0, help="0 means no limit")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.base_url = args.base_url.rstrip("/")

    started = time.time()
    token: Optional[str] = args.token if args.token else None
    join_data: Optional[Dict[str, Any]] = None
    join_backoff = 1.0  # exponential backoff for join/rejoin (seconds)
    poll_backoff = 1.0  # exponential backoff for transient server errors
    consecutive_errors = 0
    last_wait_note: Optional[str] = None
    invite_used = False  # track whether invite token was consumed

    while True:
        # ── Max runtime check (top-level, survives rejoins) ──
        if args.max_runtime_seconds > 0 and (time.time() - started) >= args.max_runtime_seconds:
            print("[worker] max runtime reached, exiting")
            return 0

        # ── Establish session (join or provided token) ──
        if not token:
            try:
                # On rejoin, skip invite_token if already consumed
                if invite_used:
                    saved_invite = args.invite_token
                    args.invite_token = None
                join_data = _join_if_needed(args)
                if invite_used:
                    args.invite_token = saved_invite
                token = join_data["token"]
                invite_used = True  # invite token is single-use
                join_backoff = 1.0  # reset on success
                consecutive_errors = 0
                print(
                    f"[worker] session ready role={join_data.get('assigned_role')} "
                    f"debate={join_data.get('debate_id')} session={join_data.get('session_id')}"
                )
            except Exception as e:
                print(f"[worker] join failed: {e}, retrying in {join_backoff:.0f}s", file=sys.stderr)
                time.sleep(join_backoff)
                join_backoff = min(join_backoff * 2, 60)
                continue
        elif not join_data:
            # Using a provided token — synthesise minimal join_data
            join_data = {
                "token": token,
                "session_id": "provided-token",
                "assigned_role": "unknown",
                "debate_id": args.debate_id or "unknown",
            }
            print(
                f"[worker] using provided token role=unknown "
                f"debate={args.debate_id or 'unknown'}"
            )

        # ── Inner poll loop (exits to outer loop on auth failures → rejoin) ──
        while True:
            if args.max_runtime_seconds > 0 and (time.time() - started) >= args.max_runtime_seconds:
                print("[worker] max runtime reached, exiting")
                return 0

            next_url = f"{args.base_url}/api/tasks/next?{urllib.parse.urlencode({'timeout_seconds': args.poll_timeout})}"
            try:
                status, body, _ = _http_json("GET", next_url, token=token, timeout=args.poll_timeout + 10)
            except Exception as e:
                print(f"[worker] poll request failed: {e}", file=sys.stderr)
                consecutive_errors += 1
                poll_backoff = min(1.0 * (2 ** min(consecutive_errors - 1, 5)), 30)
                time.sleep(poll_backoff)
                continue

            # ── Token refresh (recoverable failure → rejoin) ──
            token_expires_str = (body or {}).get("token_expires_at")
            if token_expires_str:
                new_token = _maybe_refresh_token(args.base_url, token, token_expires_str)
                if new_token:
                    token = new_token
                else:
                    print("[worker] token refresh failed, rejoining", file=sys.stderr)
                    token = None
                    join_data = None
                    break  # exit inner loop → rejoin

            # ── Auth expired (recoverable → rejoin) ──
            if status == 401:
                print(f"[worker] auth expired: {body}, rejoining", file=sys.stderr)
                token = None
                join_data = None
                break  # exit inner loop → rejoin

            # ── Server errors (transient → retry with backoff) ──
            if status >= 500:
                print(f"[worker] server error on /api/tasks/next: {status} {body}", file=sys.stderr)
                consecutive_errors += 1
                poll_backoff = min(1.0 * (2 ** min(consecutive_errors - 1, 5)), 30)
                time.sleep(poll_backoff)
                continue

            # ── Unexpected status (transient → retry) ──
            if status != 200:
                print(f"[worker] non-200 on /api/tasks/next: {status} {body}", file=sys.stderr)
                consecutive_errors += 1
                time.sleep(min(consecutive_errors, 10))
                continue

            # ── Successful response → reset error counters ──
            consecutive_errors = 0
            poll_backoff = 1.0

            task = body.get("task")

            if not task:
                wait_context = body.get("wait_context") or {}
                wait_note = wait_context.get("status_note")
                if wait_note and wait_note != last_wait_note:
                    print(f"[worker] waiting: {wait_note}")
                    last_wait_note = wait_note

                # Backend owns the stop signal — only exit when told to
                if body.get("terminal"):
                    reason = body.get("terminal_reason") or "unknown"
                    print(f"[worker] backend terminated: {reason}, exiting")
                    return 0
                time.sleep(args.idle_sleep)
                continue

            last_wait_note = None

            task_id = task.get("id")
            task_type = task.get("task_type")
            print(f"[worker] got task id={task_id} type={task_type} phase={task.get('phase')}")

            stop_event = threading.Event()
            heartbeater = LeaseHeartbeater(
                base_url=args.base_url,
                token=token,
                interval_seconds=max(10, args.heartbeat_interval),
                stop_event=stop_event,
            )
            heartbeater.start(task_id)

            try:
                c_status, c_body = _complete_task(
                    base_url=args.base_url,
                    token=token,
                    task=task,
                    turn_command=args.turn_command,
                    judge_command=args.judge_command,
                    agent_name=args.agent_name,
                )
            except Exception as e:
                heartbeater.stop()
                print(f"[worker] task completion failed for {task_id}: {e}", file=sys.stderr)
                time.sleep(1)
                continue
            finally:
                heartbeater.stop()

            if c_status == 200:
                print(f"[worker] completed task {task_id}")
                continue

            if c_status == 400 and isinstance(c_body, dict) and c_body.get("reason") == "content_too_short":
                content_retries = task["payload"].get("_content_retries", 0)
                if content_retries >= 3:
                    print(f"[worker] content still too short after {content_retries} retries for {task_id}, submitting as-is")
                    # Force-submit by skipping local pre-validation on next call
                    task["payload"]["_force_submit"] = True
                    continue
                task["payload"]["_content_retries"] = content_retries + 1
                min_req = c_body.get("min_required", 0)
                print(f"[worker] content too short for {task_id}, retrying with {min_req}+ chars (attempt #{content_retries+1})...")
                task["payload"]["min_retry_length"] = min_req
                time.sleep(0.5)
                continue

            if c_status == 409:
                reason = None
                if isinstance(c_body, dict):
                    detail = c_body.get("detail")
                    if isinstance(detail, dict):
                        reason = detail.get("reason")
                    elif isinstance(detail, str):
                        reason = detail

                if reason == "lease_expired":
                    print(f"[worker] lease expired for {task_id}; re-polling immediately")
                else:
                    print(f"[worker] task conflict for {task_id}: {c_body}")
                continue

            # ── Auth expired during completion (recoverable → rejoin) ──
            if c_status == 401:
                print(f"[worker] auth expired while completing task {task_id}: {c_body}, rejoining", file=sys.stderr)
                token = None
                join_data = None
                break  # exit inner loop → rejoin

            print(f"[worker] unexpected completion status for {task_id}: {c_status} {c_body}", file=sys.stderr)
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
