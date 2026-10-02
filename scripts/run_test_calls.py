"""Q1 scripted test calls: drives the real agent pipeline (Groq + Q2 retrieval + guards + CRM) turn by turn.

Run:  python scripts/run_test_calls.py            (all scenarios)
      python scripts/run_test_calls.py S1 S5      (re-run selected scenarios; the report is rebuilt from all saved calls)
Writes transcripts to test_logs/calls/test-<id>.json and a report to docs/Q1_TEST_CALLS.md.
Voice recordings are made separately through the browser UI; these text calls use the same /agent/turn logic.
"""
import json
import sys
import time
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import LeadQualificationAgent  # noqa: E402
from app.config import settings  # noqa: E402
from app.crm import MockCRM  # noqa: E402
from app.kb import KnowledgeBase  # noqa: E402
from app.llm import GroqService  # noqa: E402
from app.seed import seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PACE_SECONDS = 4  # stay under Groq free-tier tokens-per-minute limits

SCENARIOS = [
    {"id": "S1", "name": "Cooperative customer (couple, asks price mid-call)",
     "turns": ["Yes, that's fine.", "Hi, I'm looking for health insurance for me and my wife.", "I'm 42.",
               "We live in Austin, Texas.", "Just the two of us.", "How much does the Family Shield plan cost?",
               "No, neither of us smokes.", "No, we're both healthy.", "No, I lost my coverage when I left my job.",
               "Weekday evenings after 6 would be best.", "Yes, that's fine.", "No, that's all. Thanks!"],
     "expect": {"status": "qualified", "plan": "Family Shield", "disclosed": True, "actions": {"lead", "callback"},
                "cites_any": ["web/plans_overview.html#family-shield", "tables/plan_comparison.csv#row-2",
                              "web/family_shield.html#costs-at-a-glance"], "cites_min": 1}},
    {"id": "S2", "name": "Objections (price, employer cover, spouse)",
     "turns": ["Sure.", "I'm 30.", "Florida.", "Honestly, this sounds too expensive for me.",
               "I already get insurance through my job anyway.", "I need to talk to my wife first."],
     "expect": {"status": "in_progress", "cites_any": ["docs/sales_playbook.md#objection-it-is-too-expensive",
                                                        "docs/sales_playbook.md#objection-i-already-have-coverage-through-my-employer",
                                                        "docs/sales_playbook.md#objection-i-need-to-talk-to-my-spouse-first"]}},
    {"id": "S3", "name": "Incomplete and conflicting details",
     "turns": ["Yes.", "I'm 38.", "Ohio.", "Four of us.", "Sorry, I said that wrong, I'm actually 48.", "48 is right.",
               "I'd rather not say.", "Not sure, maybe?"],
     "expect": {"conflict_seen": "age", "final": {"age": 48}, "status": "in_progress"}},
    {"id": "S4", "name": "Out-of-scope and unsupported questions",
     "turns": ["Yes, go ahead.", "Do you cover dental implants for adults?", "Do you sell car insurance?",
               "What's the exact price for planet insurance?", "Who is the CEO of Darwix Health?"],
     "expect": {"unavailable_turns": 3, "no_invented_numbers": True}},
    {"id": "S5", "name": "Human-assistance request",
     "turns": ["Okay.", "I'm 60.", "Can I just speak to a real person please?"],
     "expect": {"status": "escalated", "actions": {"escalation"}, "cites": "docs/underwriting_policy_v2.txt#human-escalation"}},
    {"id": "S6", "name": "Not eligible (state not offered)",
     "turns": ["Yes.", "I'm 33.", "I live in California."],
     "expect": {"status": "not_eligible"}},
    {"id": "S7", "name": "Referral to senior advisor (transplant)",
     "turns": ["Yes.", "I'd like a quote. I'm 52.", "Georgia.", "Just me.", "No.", "I had a kidney transplant two years ago.", "No.",
               "Mornings.", "Yes."],
     "expect": {"status": "referred", "actions": {"lead", "callback"}}},
    {"id": "S8", "name": "Caller declines recording",
     "turns": ["No, I don't want to be recorded."],
     "expect": {"status": "recording_declined", "actions": {"callback"}}},
    {"id": "S9", "name": "Questions first, then opts in to a quote",
     "turns": ["Yes, that's fine.", "Is there a waiting period before coverage starts?", "And can I cancel if I change my mind?",
               "Okay, sure, let's see which plan fits me.", "I'm 27.", "Pennsylvania."],
     "expect": {"status": "in_progress", "cites_any": ["web/faq.html#is-there-a-waiting-period-before-my-coverage-starts",
                                                        "web/faq.html#can-i-cancel-my-policy"], "final": {"age": 27, "state": "PA"}}},
    {"id": "S10", "name": "Existing member with a denied claim",
     "turns": ["Yes.", "Hi, my claim was denied last month and I want to know why."],
     "expect": {"status": "escalated", "actions": {"escalation"}}},
    {"id": "S11", "name": "Caller outside the US (New Delhi)",
     # Said twice on purpose: a location outside the US ends the call, so the agent confirms it first.
     "turns": ["Yes, I was okay.", "I'd like a quote. I am 24 years old.", "I live in New Delhi.", "Yes, New Delhi, India."],
     "expect": {"status": "not_eligible", "cites": "docs/underwriting_policy_v2.txt#eligibility-rules"}},
    {"id": "S12", "name": "Not interested",
     "turns": ["Sure.", "Actually I'm not interested, thanks."],
     "expect": {"status": "not_interested"}},
]


def check(expect: dict, result: dict, session) -> list[tuple[str, bool]]:
    checks = []
    outcome = session.outcome["status"]
    agent_turns = [t for t in session.transcript if t["role"] == "agent"]
    refs = {r for t in agent_turns for r in t.get("citations", [])}
    if "status" in expect:
        checks.append((f"outcome is {expect['status']} (got {outcome})", outcome == expect["status"]))
    if "plan" in expect:
        checks.append((f"{expect['plan']} offered", expect["plan"] in session.outcome.get("plans", [])))
    if expect.get("disclosed"):
        # The rule is conditional: a stated price must be preceded by the disclosure.
        priced = [t for t in agent_turns if "$" in t["text"]]
        ok = session.underwriting_disclosed if priced else True
        checks.append((f"underwriting disclosure precedes any price ({len(priced)} priced turns)", ok))
    if "actions" in expect:
        kinds = {a["type"] for a in session.actions}
        checks.append((f"CRM actions {sorted(expect['actions'])} (got {sorted(kinds)})", expect["actions"] <= kinds))
    if "cites" in expect:
        checks.append((f"cited {expect['cites']}", expect["cites"] in refs))
    if "cites_any" in expect:
        hit = [r for r in expect["cites_any"] if r in refs]
        need = expect.get("cites_min", 2)
        checks.append((f"knowledge-base guidance cited ({len(hit)} of {len(expect['cites_any'])}, need {need})", len(hit) >= need))
    if "conflict_seen" in expect:
        asked = any("which one is correct" in t["text"].lower() or "earlier" in t["text"].lower() for t in agent_turns)
        checks.append(("conflicting age read back to caller", asked))
    if "final" in expect:
        for k, v in expect["final"].items():
            checks.append((f"final {k} = {v} (got {session.lead.get(k)})", session.lead.get(k) == v))
    if "unavailable_turns" in expect:
        said = sum("verified information" in t["text"].lower() or "don't have" in t["text"].lower() for t in agent_turns)
        checks.append((f"said information unavailable in >= {expect['unavailable_turns']} turns (got {said})",
                       said >= expect["unavailable_turns"]))
    if expect.get("no_invented_numbers"):
        priced = [t["text"] for t in agent_turns if "$" in t["text"]]
        checks.append(("no prices or amounts stated for unsupported products", not priced))
    return checks


def main(selected: list[str]) -> None:
    if not settings.groq_api_key:
        print("GROQ_API_KEY not set: running with the rule-based fallback brain.")
    kb = KnowledgeBase()
    seed(kb)
    agent = LeadQualificationAgent(kb, GroqService(), MockCRM())
    report = ["# Question 1: Scripted Test Calls", "",
              f"Generated by `python scripts/run_test_calls.py` with model `{settings.groq_model}` "
              f"({'Groq' if settings.groq_api_key else 'fallback rules'}). Each call runs through the same pipeline as "
              "`/agent/turn`: Q2 grounded retrieval, the LLM, code guards, rules and the mock CRM. "
              "Transcripts: `test_logs/calls/test-<id>.json`.", ""]
    log_dir = ROOT / "test_logs" / "calls"
    for scenario in SCENARIOS:
        if selected and scenario["id"] not in selected:
            continue
        call_id = f"test-{scenario['id'].lower()}"
        agent.start(call_id)
        for text in scenario["turns"]:
            if settings.groq_api_key:
                time.sleep(PACE_SECONDS)
            if agent.turn(call_id, text)["end_call"]:
                break
        agent.end(call_id)
        print(f"{scenario['id']} done: {agent.sessions[call_id].outcome['status']}", flush=True)

    # The report always covers every scenario, from the saved call logs.
    summary = []
    for scenario in SCENARIOS:
        path = log_dir / f"test-{scenario['id'].lower()}.json"
        if not path.exists():
            continue
        session = SimpleNamespace(**json.loads(path.read_text()))
        checks = check(scenario["expect"], {}, session)
        fallback_turns = sum(e["guard"] == "llm_error" for e in session.guard_events)
        checks.append((f"every turn answered by the live LLM ({fallback_turns} fell back to rules)", fallback_turns == 0))
        passed = all(ok for _, ok in checks)
        summary.append((scenario, passed, session))
        lines = [f"## {scenario['id']}: {scenario['name']} ({'PASS' if passed else 'FAIL'})", "",
                 f"Recorded {session.started_at}", "", "| # | Speaker | Text | Citations |", "|---|---|---|---|"]
        for n, t in enumerate(session.transcript, 1):
            cites = "<br>".join(f"`{c}`" for c in t.get("citations", []))
            lines.append(f"| {n} | {t['role']} | {t['text']} | {cites} |")
        lines += ["", "**Checks**", ""] + [f"- {'✅' if ok else '❌'} {name}" for name, ok in checks]
        lines += ["", f"**Outcome:** `{session.outcome['status']}`: {session.outcome.get('reason', '')}",
                  f"**Lead fields:** `{json.dumps(session.lead)}`",
                  f"**CRM actions:** {', '.join(a['type'] + ' ' + a['operation'] for a in session.actions) or 'none'}",
                  f"**Guard events:** {', '.join(sorted(set(e['guard'] for e in session.guard_events))) or 'none'}", ""]
        report += lines
        print(f"{scenario['id']} {'PASS' if passed else 'FAIL'} {session.outcome['status']}")
        for name, ok in checks:
            if not ok:
                print("   failed:", name)
    rate_limited = sum("RateLimit" in e.get("error", "") for _, _, sess in summary for e in sess.guard_events)
    table = ["## Summary", ""]
    if rate_limited:
        table += [f"> ⚠️ **This run is not valid evidence.** {rate_limited} turns fell back to the rule-based brain "
                  f"because the Groq daily token quota was exhausted. Re-run once quota resets.", ""]
    table += ["| Scenario | Result | Outcome | Turns |", "|---|---|---|---|"]
    table += [f"| {s['id']} {s['name']} | {'PASS' if ok else 'FAIL'} | {sess.outcome['status']} | {len(sess.transcript)} |"
              for s, ok, sess in summary]
    report[5:5] = table + [""]
    (ROOT / "docs" / "Q1_TEST_CALLS.md").write_text("\n".join(report))


if __name__ == "__main__":
    main(sys.argv[1:])
