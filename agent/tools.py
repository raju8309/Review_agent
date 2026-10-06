"""
Investigation tools.

Every tool call is recorded in an EvidenceLog and given a source_id
(S1, S2, ...). The agent must cite a source_id for each claim it makes.
That is what makes the citation check in evals/ possible: we can go back
to the exact record the agent says it relied on and check whether it
actually says what the agent claims.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

DATA = Path(__file__).parent.parent / "data"

CUSTOMERS = {c["customer_id"]: c for c in json.loads((DATA / "customers.json").read_text())}
TRANSACTIONS = json.loads((DATA / "transactions.json").read_text())
POLICIES = json.loads((DATA / "policies.json").read_text())
WATCHLIST = json.loads((DATA / "watchlist.json").read_text())

BY_TXN = {t["transaction_id"]: t for t in TRANSACTIONS}


@dataclass
class EvidenceLog:
    """Records every tool call so claims can be traced back to a record."""
    sources: dict[str, dict] = field(default_factory=dict)
    _n: int = 0

    def record(self, tool: str, args: dict, result: Any) -> str:
        self._n += 1
        sid = f"S{self._n}"
        self.sources[sid] = {"source_id": sid, "tool": tool, "args": args, "result": result}
        return sid

    def get(self, sid: str) -> Optional[dict]:
        return self.sources.get(sid)

    def as_list(self) -> list[dict]:
        return list(self.sources.values())


class Tools:
    def __init__(self, log: EvidenceLog):
        self.log = log

    # ---------------------------------------------------------------- customer
    def get_customer(self, customer_id: str) -> dict:
        """Profile, stated income, KYC status and prior SAR history."""
        c = CUSTOMERS.get(customer_id)
        result = c if c else {"error": f"no customer {customer_id}"}
        sid = self.log.record("get_customer", {"customer_id": customer_id}, result)
        return {"source_id": sid, **result}

    # ------------------------------------------------------------ transactions
    def get_transactions(self, customer_id: str, days_back: int = 30,
                         as_of: Optional[str] = None) -> dict:
        """Transactions for a customer in a window, plus totals for that window."""
        anchor = date.fromisoformat(as_of) if as_of else date(2026, 8, 28)
        start = anchor - timedelta(days=days_back)
        rows = [t for t in TRANSACTIONS
                if t["customer_id"] == customer_id
                and start <= date.fromisoformat(t["date"]) <= anchor]
        rows.sort(key=lambda t: t["date"])
        result = {
            "customer_id": customer_id,
            "window_start": start.isoformat(),
            "window_end": anchor.isoformat(),
            "transaction_count": len(rows),
            "total_in": round(sum(t["amount"] for t in rows if t["direction"] == "in"), 2),
            "total_out": round(sum(t["amount"] for t in rows if t["direction"] == "out"), 2),
            "transactions": rows[:80],
        }
        sid = self.log.record("get_transactions",
                              {"customer_id": customer_id, "days_back": days_back,
                               "as_of": anchor.isoformat()}, result)
        return {"source_id": sid, **result}

    # ----------------------------------------------------------------- policy
    def search_policy(self, query: str) -> dict:
        """Keyword search over AML policy documents."""
        q = {w for w in query.lower().split() if len(w) > 3}
        scored = []
        for p in POLICIES:
            blob = (p["title"] + " " + p["text"]).lower()
            score = sum(1 for w in q if w in blob)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        hits = [p for _, p in scored[:3]]
        result = {"query": query, "matches": hits}
        sid = self.log.record("search_policy", {"query": query}, result)
        return {"source_id": sid, **result}

    # -------------------------------------------------------------- watchlist
    def check_watchlist(self, name: str) -> dict:
        """Screen a counterparty name against the sanctions watchlist."""
        n = name.upper().strip()
        exact = [e for e in WATCHLIST if e["name"].upper() == n]
        partial = [e for e in WATCHLIST
                   if not exact and (n in e["name"].upper() or e["name"].upper() in n)]
        result = {
            "query": name,
            "match": bool(exact or partial),
            "match_type": "exact" if exact else ("partial" if partial else "none"),
            "entries": exact or partial,
        }
        sid = self.log.record("check_watchlist", {"name": name}, result)
        return {"source_id": sid, **result}


TOOL_SPECS = [
    {
        "name": "get_customer",
        "description": "Look up a customer's profile: occupation, stated monthly income, "
                       "typical monthly volume, KYC status and prior SAR history.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_transactions",
        "description": "Get a customer's transactions in a window ending at the alert date, "
                       "with totals in and out. Use this to compare recent activity against "
                       "the customer's normal behaviour.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "days_back": {"type": "integer", "description": "window length, default 30"},
                "as_of": {"type": "string", "description": "YYYY-MM-DD, the alert date"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "search_policy",
        "description": "Search the bank's AML and fraud policy documents. Use this to find "
                       "the rule that governs the decision before making it.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "check_watchlist",
        "description": "Screen a counterparty name against the sanctions watchlist.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]


def alert_for(transaction_id: str) -> dict:
    """Build the alert payload a bank's monitoring system would hand the analyst."""
    t = BY_TXN[transaction_id]
    return {
        "alert_id": f"A-{transaction_id}",
        "transaction": t,
        "customer_id": t["customer_id"],
        "raised_on": t["date"],
        "reason": "Automated monitoring rule triggered on this transaction.",
    }
