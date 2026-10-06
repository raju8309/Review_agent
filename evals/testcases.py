"""
Labeled test alerts.

The planted patterns give us ground truth for the decision. We add a sample of
ordinary transactions that should be cleared, so the agent is scored on both
catching real problems and not crying wolf.
"""

import json
import random
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

EXPECTED = {
    "SPIKE": "escalate",
    "STRUCTURING": "escalate",
    "CIRCULAR": "escalate",
    "WATCHLIST": "escalate",
    "BENIGN": "clear",
    "NORMAL": "clear",
}

# the policy an analyst should land on, used as a soft check only
EXPECTED_POLICY = {
    "SPIKE": "AML-002",
    "STRUCTURING": "AML-001",
    "CIRCULAR": "AML-004",
    "WATCHLIST": "AML-003",
    "BENIGN": "AML-002",
    "NORMAL": "AML-002",
}


def build(n_normal: int = 12, seed: int = 7) -> list[dict]:
    random.seed(seed)
    planted = json.loads((DATA / "planted.json").read_text())
    txns = json.loads((DATA / "transactions.json").read_text())

    cases = [
        {"transaction_id": tid, "pattern": pat,
         "expected_decision": EXPECTED[pat], "expected_policy": EXPECTED_POLICY[pat]}
        for tid, pat in planted.items()
    ]

    # ordinary activity: mid-sized, unremarkable, should be cleared
    ordinary = [t for t in txns
                if t["transaction_id"] not in planted
                and 500 < t["amount"] < 4000]
    for t in random.sample(ordinary, min(n_normal, len(ordinary))):
        cases.append({"transaction_id": t["transaction_id"], "pattern": "NORMAL",
                      "expected_decision": "clear", "expected_policy": "AML-002"})

    random.shuffle(cases)
    return cases


if __name__ == "__main__":
    cs = build()
    print(f"{len(cs)} test cases")
    from collections import Counter
    for pat, n in Counter(c["pattern"] for c in cs).most_common():
        print(f"  {pat:<12} {n}")
