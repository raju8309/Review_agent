"""
Citation verification.

For each claim the agent makes, check whether the record it cited actually
supports it. Three outcomes:

  SUPPORTED    - every checkable token in the claim appears in the cited source
  WRONG_SOURCE - the facts are true but live in a different source the agent
                 retrieved; it cited the wrong record
  UNSUPPORTED  - the facts appear in no retrieved source at all

The distinction matters for a bank. WRONG_SOURCE means the audit trail is
broken even though the answer was right. UNSUPPORTED means the agent made
something up.

Checking is deliberately mechanical rather than LLM-judged, so the number is
reproducible and you can show your working.
"""

import json
import re

MONEY = re.compile(r"\$?\s?([\d]{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}|\d{4,})")
# capitalised multi-word entity names, e.g. VOSTOK TRADING OOO
ENTITY = re.compile(r"\b[A-Z][A-Z0-9&\.\-]{2,}(?:\s+[A-Z][A-Z0-9&\.\-]{1,}){0,4}\b")
IDS = re.compile(r"\b(?:C\d{4}|T\d{5}|AML-\d{3})\b")

STOP = {"THE", "AND", "FOR", "WITH", "THIS", "THAT", "POLICY", "CUSTOMER",
        "TRANSACTION", "ACCOUNT", "ALERT", "SAR", "KYC", "CTR", "OFAC", "SDN",
        "ESCALATE", "CLEAR", "ESCALATION"}


def _norm_money(s: str) -> str:
    return s.replace(",", "").replace("$", "").strip().rstrip("0").rstrip(".")


def _tokens(claim: str) -> dict[str, set[str]]:
    money = {_norm_money(m) for m in MONEY.findall(claim)}
    money = {m for m in money if m and len(m) >= 3}
    ents = {e.strip() for e in ENTITY.findall(claim)}
    ents = {e for e in ents if e not in STOP and len(e) > 3}
    ids = set(IDS.findall(claim))
    return {"money": money, "entities": ents, "ids": ids}


def _blob(source: dict) -> str:
    return json.dumps(source.get("result", {}), default=str)


def _supported_by(claim_tokens: dict, blob: str) -> bool:
    up = blob.upper()
    flat = blob.replace(",", "")
    for m in claim_tokens["money"]:
        # tolerate rounding: match the first 4 significant digits
        head = m.split(".")[0]
        probe = head[:4] if len(head) >= 4 else head
        if probe and probe not in flat.replace("$", ""):
            return False
    for e in claim_tokens["entities"]:
        if e.upper() not in up:
            return False
    for i in claim_tokens["ids"]:
        if i.upper() not in up:
            return False
    return True


def verify_claim(claim: dict, evidence: list[dict]) -> dict:
    by_id = {s["source_id"]: s for s in evidence}
    cited_id = claim.get("source_id")
    text = claim.get("claim", "")
    toks = _tokens(text)

    cited = by_id.get(cited_id)
    if cited is None:
        return {"verdict": "UNSUPPORTED", "cited": cited_id,
                "note": "cited a source_id that was never returned by a tool"}

    checkable = sum(len(v) for v in toks.values())

    if checkable == 0:
        # No numbers or entity names to match. Fall back to a weaker check:
        # do the distinctive words of the claim appear in the cited record?
        words = {w.lower().strip(".,;:()") for w in text.split() if len(w) > 4}
        words -= {w.lower() for w in STOP}
        if not words:
            return {"verdict": "NOT_CHECKABLE", "cited": cited_id,
                    "note": "nothing specific enough to verify"}
        blob = _blob(cited).lower()
        hits = sum(1 for w in words if w in blob)
        if hits / len(words) >= 0.5:
            return {"verdict": "SUPPORTED", "cited": cited_id,
                    "note": f"keyword overlap {hits}/{len(words)} (weak check)"}
        for sid, src in by_id.items():
            if sid == cited_id:
                continue
            b2 = _blob(src).lower()
            if sum(1 for w in words if w in b2) / len(words) >= 0.5:
                return {"verdict": "WRONG_SOURCE", "cited": cited_id,
                        "note": f"wording matches {sid}, not {cited_id} (weak check)"}
        return {"verdict": "UNSUPPORTED", "cited": cited_id,
                "note": f"keyword overlap only {hits}/{len(words)} in any record"}

    if _supported_by(toks, _blob(cited)):
        return {"verdict": "SUPPORTED", "cited": cited_id, "note": ""}

    for sid, src in by_id.items():
        if sid != cited_id and _supported_by(toks, _blob(src)):
            return {"verdict": "WRONG_SOURCE", "cited": cited_id,
                    "note": f"facts are in {sid}, not {cited_id}"}

    return {"verdict": "UNSUPPORTED", "cited": cited_id,
            "note": "facts appear in no retrieved record"}


def verify_case(case: dict, evidence: list[dict]) -> dict:
    claims = case.get("claims", []) if case else []
    results = [{**verify_claim(c, evidence), "claim": c.get("claim", "")} for c in claims]
    checkable = [r for r in results if r["verdict"] != "NOT_CHECKABLE"]
    supported = [r for r in checkable if r["verdict"] == "SUPPORTED"]
    return {
        "claims_total": len(results),
        "claims_checkable": len(checkable),
        "claims_supported": len(supported),
        "support_rate": (len(supported) / len(checkable)) if checkable else None,
        "has_broken_citation": any(r["verdict"] in ("WRONG_SOURCE", "UNSUPPORTED")
                                   for r in checkable),
        "details": results,
    }
