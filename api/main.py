"""
Review queue API.

  uvicorn api.main:app --reload
  open http://localhost:8000

MOCK=1 runs the rule-based investigator so you can click through the UI
without an API key.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.investigator import investigate
from agent.tools import alert_for
from evals.testcases import build
from evals.verify import verify_case

MOCK = os.getenv("MOCK", "0") == "1"
STATIC = Path(__file__).parent.parent / "static"

app = FastAPI(title="Case Review Agent")

QUEUE = build()
DECISIONS: dict[str, dict] = {}
CACHE: dict[str, dict] = {}


class Review(BaseModel):
    transaction_id: str
    action: str          # "approve" | "reject"
    note: str = ""


@app.get("/api/queue")
def queue():
    return [
        {
            "transaction_id": c["transaction_id"],
            "pattern": c["pattern"],
            "alert": alert_for(c["transaction_id"]),
            "reviewed": c["transaction_id"] in DECISIONS,
            "analyst_action": DECISIONS.get(c["transaction_id"], {}).get("action"),
        }
        for c in QUEUE
    ]


@app.get("/api/case/{transaction_id}")
def case(transaction_id: str):
    if transaction_id in CACHE:
        return CACHE[transaction_id]

    out = investigate(alert_for(transaction_id), mock=MOCK)
    ver = verify_case(out.get("case"), out["evidence"])
    payload = {
        "transaction_id": transaction_id,
        "alert": alert_for(transaction_id),
        "case": out.get("case"),
        "evidence": out["evidence"],
        "verification": ver,
        "turns": out.get("turns"),
    }
    CACHE[transaction_id] = payload
    return payload


@app.post("/api/review")
def review(r: Review):
    DECISIONS[r.transaction_id] = {"action": r.action, "note": r.note}
    return {"ok": True, "recorded": DECISIONS[r.transaction_id]}


@app.get("/api/stats")
def stats():
    path = Path(__file__).parent.parent / "evals" / "results.json"
    if not path.exists():
        return {"ran": False}
    rows = json.loads(path.read_text())
    n = len(rows)
    correct = sum(1 for r in rows if r["correct"])
    checkable = sum(r["verification"]["claims_checkable"] for r in rows)
    supported = sum(r["verification"]["claims_supported"] for r in rows)
    return {
        "ran": True,
        "cases": n,
        "decision_accuracy": round(correct / n, 3) if n else None,
        "citation_support_rate": round(supported / checkable, 3) if checkable else None,
        "claims_checked": checkable,
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
