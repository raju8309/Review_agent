"""
Run the eval.

  python -m evals.run_eval --mock          # no API key needed
  python -m evals.run_eval                 # real agent
  python -m evals.run_eval --limit 5

Reports two things:
  1. decision accuracy      - did it reach the right call
  2. citation support rate  - did its stated reasons actually match the records
                              it retrieved

The second number is the point. A case can be decided correctly and still be
unusable for audit, because the justification cites something that does not
say what the agent claims.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from agent.investigator import investigate
from agent.tools import alert_for
from evals.testcases import build
from evals.verify import verify_case

OUT = Path(__file__).parent / "results.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    cases = build()
    if a.limit:
        cases = cases[:a.limit]

    rows = []
    for i, tc in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {tc['transaction_id']}  {tc['pattern']}", flush=True)
        out = investigate(alert_for(tc["transaction_id"]),
                          mock=a.mock, verbose=a.verbose)
        case = out.get("case")
        decision = (case or {}).get("decision")
        ver = verify_case(case, out["evidence"])
        rows.append({
            **tc,
            "decision": decision,
            "correct": decision == tc["expected_decision"],
            "policy_cited": (case or {}).get("policy_cited"),
            "policy_match": (case or {}).get("policy_cited") == tc["expected_policy"],
            "turns": out.get("turns"),
            "verification": ver,
            "case": case,
            "evidence": out["evidence"],
        })

    OUT.write_text(json.dumps(rows, indent=2, default=str))
    report(rows)


def report(rows):
    n = len(rows)
    correct = sum(1 for r in rows if r["correct"])

    checkable = sum(r["verification"]["claims_checkable"] for r in rows)
    supported = sum(r["verification"]["claims_supported"] for r in rows)
    verdicts = Counter(d["verdict"]
                       for r in rows for d in r["verification"]["details"])

    clean = [r for r in rows
             if r["correct"] and not r["verification"]["has_broken_citation"]]

    print("\n" + "=" * 62)
    print("RESULTS")
    print("=" * 62)
    print(f"cases                      {n}")
    print(f"decision accuracy          {correct}/{n}  ({correct/n:.0%})")
    if checkable:
        print(f"claims checked             {checkable}")
        print(f"citations supported        {supported}/{checkable}  "
              f"({supported/checkable:.0%})")
    print(f"right call AND clean trail {len(clean)}/{n}  ({len(clean)/n:.0%})")

    print("\nclaim verdicts")
    for v, c in verdicts.most_common():
        print(f"  {v:<15} {c}")

    print("\nby pattern")
    by = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        b = by[r["pattern"]]
        b[0] += 1
        b[1] += int(r["correct"])
        b[2] += int(r["verification"]["has_broken_citation"])
    for pat, (tot, ok, broken) in sorted(by.items()):
        print(f"  {pat:<12} {ok}/{tot} correct   {broken} with a broken citation")

    bad = [r for r in rows if not r["correct"]]
    if bad:
        print("\nwrong decisions")
        for r in bad:
            print(f"  {r['transaction_id']} {r['pattern']}: "
                  f"said {r['decision']}, expected {r['expected_decision']}")

    broken = [(r, d) for r in rows for d in r["verification"]["details"]
              if d["verdict"] in ("WRONG_SOURCE", "UNSUPPORTED")]
    if broken:
        print("\nbroken citations (first 5)")
        for r, d in broken[:5]:
            print(f"  {r['transaction_id']} [{d['verdict']}] {d['claim'][:70]}")
            print(f"      {d['note']}")

    print(f"\nfull results: {OUT}")


if __name__ == "__main__":
    main()
