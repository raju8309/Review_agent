"""
The investigation agent.

Takes an alert, decides what to look up, and produces a structured case file
where every claim carries the source_id of the record it came from.

Run with --mock to exercise the whole pipeline without an API key.
"""

import json
import os
import re
from typing import Any, Optional

from .tools import Tools, EvidenceLog, TOOL_SPECS

MODEL = os.getenv("MODEL", "claude-sonnet-4-5")
MAX_TURNS = 10

SYSTEM = """You are an AML analyst agent at a bank. You investigate a flagged \
transaction and decide whether to ESCALATE it to compliance or CLEAR it.

How to work:
1. Look up the customer so you know what normal looks like for them.
2. Pull their recent transactions and compare against that baseline.
3. If the counterparty is a wire or an entity name, screen it against the watchlist.
4. Find the policy that governs this situation BEFORE you decide. Do not rely on \
general knowledge of AML rules - the bank's own policy is what matters.
5. Then decide.

Rules that matter:
- Unusual volume alone is not enough to escalate. If the counterparty identifies a \
legitimate source (payroll, tax refund, insurance, property, vehicle purchase), the \
policy says clear it.
- A watchlist match is a mandatory escalation.
- You must cite a specific source_id for every factual claim you make. A source_id is \
returned by each tool call. Never state a fact you did not retrieve.

When you are done investigating, reply with ONLY a JSON object in this shape:

{
  "decision": "escalate" | "clear",
  "policy_cited": "AML-00X",
  "claims": [
    {"claim": "<one specific factual statement>", "source_id": "<S#>"},
    ...
  ],
  "summary": "<2-3 sentences a compliance officer would read>"
}

Each claim must be a single checkable fact, for example "The customer's stated \
typical monthly volume is $5,000" or "The counterparty VOSTOK TRADING OOO is an \
exact match on the OFAC-SDN list". Do not bundle several facts into one claim.
"""


def _extract_json(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def investigate(alert: dict, mock: bool = False, verbose: bool = False) -> dict:
    """Run the agent on one alert. Returns {case, evidence, turns}."""
    log = EvidenceLog()
    tools = Tools(log)

    if mock:
        return _mock_investigate(alert, tools, log)

    from anthropic import Anthropic
    client = Anthropic()

    user = (
        f"Alert {alert['alert_id']} raised on {alert['raised_on']}.\n\n"
        f"Flagged transaction:\n{json.dumps(alert['transaction'], indent=2)}\n\n"
        f"Investigate and decide."
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    turns = 0

    while turns < MAX_TURNS:
        turns += 1
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            tools=TOOL_SPECS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in resp.content if b.type == "text")
            case = _extract_json(text)
            if case is None:
                messages.append({"role": "user",
                                 "content": "Reply with only the JSON case file."})
                continue
            return {"case": case, "evidence": log.as_list(), "turns": turns}

        results = []
        for tu in tool_uses:
            fn = getattr(tools, tu.name, None)
            out = fn(**tu.input) if fn else {"error": f"unknown tool {tu.name}"}
            if verbose:
                print(f"  -> {tu.name}({tu.input}) = {out.get('source_id')}")
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(out, default=str)[:6000],
            })
        messages.append({"role": "user", "content": results})

    return {"case": None, "evidence": log.as_list(), "turns": turns,
            "error": "max turns reached"}


# --------------------------------------------------------------------------
# Mock mode: deterministic rule-based investigator so you can run the whole
# pipeline, the API and the UI without an API key. It deliberately makes a
# couple of sloppy citations so the eval has something to catch.
# --------------------------------------------------------------------------
def _mock_investigate(alert: dict, tools: Tools, log: EvidenceLog) -> dict:
    t = alert["transaction"]
    cid = alert["customer_id"]

    cust = tools.get_customer(cid)
    hist = tools.get_transactions(cid, days_back=30, as_of=alert["raised_on"])
    wl = tools.check_watchlist(t["counterparty"])

    claims = []
    if wl["match"]:
        pol = tools.search_policy("sanctions watchlist screening escalation")
        claims = [
            {"claim": f"The counterparty {t['counterparty']} matches the sanctions "
                      f"watchlist ({wl['match_type']} match).", "source_id": wl["source_id"]},
            {"claim": "Policy requires mandatory escalation on a watchlist match.",
             "source_id": pol["source_id"]},
        ]
        case = {"decision": "escalate", "policy_cited": "AML-003", "claims": claims,
                "summary": "Counterparty is on the sanctions watchlist. Mandatory escalation."}
        return {"case": case, "evidence": log.as_list(), "turns": 4}

    baseline = cust.get("typical_monthly_volume", 0)
    moved = max(hist["total_in"], hist["total_out"])
    benign_markers = ("PAYROLL", "TAX REF", "ESCROW", "INSURANCE", "TUITION", "DEALER")
    looks_benign = any(m in t["counterparty"].upper() for m in benign_markers)

    pol = tools.search_policy("unusual volume relative to customer profile explanation")
    claims = [
        {"claim": f"The customer's stated typical monthly volume is ${baseline:,}.",
         "source_id": cust["source_id"]},
        {"claim": f"Activity in the 30 days to {alert['raised_on']} totalled "
                  f"${moved:,.2f}.", "source_id": hist["source_id"]},
        {"claim": f"The counterparty on the flagged transaction is {t['counterparty']}.",
         "source_id": hist["source_id"]},   # deliberately the wrong source
    ]

    if looks_benign:
        case = {"decision": "clear", "policy_cited": "AML-002", "claims": claims,
                "summary": "Volume is above baseline but the counterparty identifies a "
                           "legitimate source, which policy says is grounds to clear."}
    elif moved > baseline * 5:
        case = {"decision": "escalate", "policy_cited": "AML-002", "claims": claims,
                "summary": "Volume materially exceeds the customer's stated baseline with "
                           "no documented explanation."}
    else:
        case = {"decision": "clear", "policy_cited": "AML-002", "claims": claims,
                "summary": "Activity is within the customer's normal range."}
    return {"case": case, "evidence": log.as_list(), "turns": 4}


if __name__ == "__main__":
    import argparse
    from .tools import alert_for

    ap = argparse.ArgumentParser()
    ap.add_argument("transaction_id")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()

    out = investigate(alert_for(a.transaction_id), mock=a.mock, verbose=True)
    print(json.dumps(out["case"], indent=2))
