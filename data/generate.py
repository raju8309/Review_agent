"""
Synthetic bank data with planted patterns.

Produces:
  customers.json   - 30 customers with profiles and risk attributes
  transactions.json- ~1200 transactions, some carrying deliberate patterns
  policies.json    - AML / fraud policy documents the agent can search
  watchlist.json   - a fake sanctions list

Planted patterns (the agent should find these):
  SPIKE      - sudden volume far above the customer's own baseline
  STRUCTURING- repeated deposits just under the $10,000 reporting threshold
  CIRCULAR   - money moving in a loop between a small group of accounts
  WATCHLIST  - counterparty appears on the sanctions list
  BENIGN     - looks odd but is explainable (bonus, tax refund, house deposit)
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).parent

FIRST = ["Maria", "James", "Wei", "Priya", "Omar", "Sofia", "Daniel", "Aisha",
         "Lucas", "Fatima", "Noah", "Elena", "Rahul", "Grace", "Tomas",
         "Yuki", "Hassan", "Clara", "Ivan", "Nadia", "Peter", "Rosa",
         "Samuel", "Leila", "Marcus", "Anna", "Jorge", "Mei", "David", "Zara"]
LAST = ["Alvarez", "Chen", "Okafor", "Patel", "Haddad", "Rossi", "Novak",
        "Ibrahim", "Silva", "Khan", "Berg", "Petrov", "Sharma", "Mensah",
        "Kovac", "Tanaka", "Ali", "Dubois", "Sokolov", "Rahman", "Muller",
        "Garcia", "Osei", "Farah", "Lindqvist", "Weber", "Mendez", "Lin",
        "Thompson", "Hussein"]

MERCHANTS = ["Whole Foods", "Shell", "Amazon", "Delta Airlines", "CVS",
             "Home Depot", "Uber", "Starbucks", "Target", "Verizon"]

OCCUPATIONS = ["teacher", "software engineer", "nurse", "restaurant owner",
               "contractor", "retail manager", "accountant", "driver",
               "import/export trader", "freelance consultant"]


def make_customers():
    customers = []
    for i in range(30):
        cid = f"C{1000 + i}"
        occ = random.choice(OCCUPATIONS)
        # monthly baseline spend, used later to judge what "normal" means
        baseline = random.choice([2_000, 3_500, 5_000, 8_000, 15_000])
        customers.append({
            "customer_id": cid,
            "name": f"{FIRST[i]} {LAST[i]}",
            "account_opened": (datetime(2019, 1, 1) +
                               timedelta(days=random.randint(0, 2000))).date().isoformat(),
            "occupation": occ,
            "stated_monthly_income": baseline,
            "typical_monthly_volume": baseline,
            "country": random.choice(["US"] * 8 + ["US", "US"]),
            "kyc_status": random.choice(["verified"] * 9 + ["pending_refresh"]),
            "prior_sars_filed": random.choice([0] * 9 + [1]),
        })
    return customers


def txn(tid, cid, date, amount, direction, counterparty, channel="ach"):
    return {
        "transaction_id": tid,
        "customer_id": cid,
        "date": date.date().isoformat(),
        "amount": round(amount, 2),
        "direction": direction,          # "in" or "out"
        "counterparty": counterparty,
        "channel": channel,              # ach | wire | card | cash
    }


def make_transactions(customers):
    txns = []
    n = [0]
    base = datetime(2026, 1, 1)

    def nid():
        n[0] += 1
        return f"T{10000 + n[0]}"

    # ---- ordinary background activity for everyone ----
    for c in customers:
        monthly = c["typical_monthly_volume"]
        for day in range(240):
            if random.random() < 0.55:
                amt = abs(random.gauss(monthly / 30, monthly / 90))
                txns.append(txn(nid(), c["customer_id"], base + timedelta(days=day),
                                max(8, amt), "out", random.choice(MERCHANTS), "card"))
        # salary twice a month
        for m in range(8):
            for d in (1, 15):
                txns.append(txn(nid(), c["customer_id"],
                                base + timedelta(days=m * 30 + d),
                                c["stated_monthly_income"] / 2, "in",
                                "EMPLOYER PAYROLL", "ach"))

    # ---- planted patterns ----
    planted = {}

    # SPIKE: customers 0 and 1 suddenly move ~12x their baseline
    for c in customers[0:2]:
        day = random.randint(180, 220)
        amt = c["typical_monthly_volume"] * 12
        t = txn(nid(), c["customer_id"], base + timedelta(days=day), amt,
                "out", "OFFSHORE HOLDINGS LTD", "wire")
        txns.append(t)
        planted[t["transaction_id"]] = "SPIKE"

    # STRUCTURING: customers 2 and 3 deposit just under $10k, repeatedly
    for c in customers[2:4]:
        start = random.randint(150, 180)
        for k in range(6):
            amt = random.uniform(9_100, 9_850)
            t = txn(nid(), c["customer_id"], base + timedelta(days=start + k * 2),
                    amt, "in", "CASH DEPOSIT", "cash")
            txns.append(t)
            if k == 5:
                planted[t["transaction_id"]] = "STRUCTURING"

    # CIRCULAR: customers 4,5,6 pass the same money around a loop
    loop = [c["customer_id"] for c in customers[4:7]]
    amt = 47_500
    for k, cid in enumerate(loop):
        nxt = loop[(k + 1) % len(loop)]
        d = base + timedelta(days=200 + k)
        txns.append(txn(nid(), cid, d, amt, "out", nxt, "wire"))
        t = txn(nid(), nxt, d, amt, "in", cid, "wire")
        txns.append(t)
        if k == len(loop) - 1:
            planted[t["transaction_id"]] = "CIRCULAR"

    # WATCHLIST: customers 7 and 8 pay a sanctioned entity
    for c, name in zip(customers[7:9], ["VOSTOK TRADING OOO", "ARDEN MARITIME SA"]):
        t = txn(nid(), c["customer_id"], base + timedelta(days=random.randint(190, 230)),
                random.uniform(20_000, 60_000), "out", name, "wire")
        txns.append(t)
        planted[t["transaction_id"]] = "WATCHLIST"

    # BENIGN but odd-looking: large one-offs with an obvious explanation
    benign = [
        ("ANNUAL BONUS - EMPLOYER PAYROLL", "in", "ach"),
        ("IRS TREAS 310 TAX REF", "in", "ach"),
        ("FIRST AMERICAN TITLE - ESCROW", "out", "wire"),
        ("STATE FARM INSURANCE CLAIM", "in", "ach"),
        ("UNIVERSITY TUITION PAYMENT", "out", "ach"),
        ("CERTIFIED DEALER - VEHICLE PURCHASE", "out", "ach"),
    ]
    for c, (cp, direction, ch) in zip(customers[9:15], benign):
        t = txn(nid(), c["customer_id"], base + timedelta(days=random.randint(190, 230)),
                c["typical_monthly_volume"] * random.uniform(3, 6), direction, cp, ch)
        txns.append(t)
        planted[t["transaction_id"]] = "BENIGN"

    txns.sort(key=lambda t: t["date"])
    return txns, planted


POLICIES = [
    {
        "policy_id": "AML-001",
        "title": "Currency Transaction Reporting Threshold",
        "text": ("Any cash transaction exceeding $10,000 in a single business day must be "
                 "reported on a Currency Transaction Report (CTR). Multiple cash deposits "
                 "by the same customer that individually fall below $10,000 but aggregate "
                 "above it within a rolling 5-day window must be reviewed for structuring. "
                 "Structuring is the deliberate splitting of transactions to evade reporting "
                 "and is itself a reportable offence regardless of the source of funds."),
    },
    {
        "policy_id": "AML-002",
        "title": "Unusual Volume Relative to Customer Profile",
        "text": ("Activity is considered anomalous when a customer's transaction volume in a "
                 "30-day period exceeds 5x their stated typical monthly volume. Anomalous "
                 "volume alone is not sufficient grounds to escalate. The analyst must first "
                 "establish whether a documented explanation exists, such as payroll bonus, "
                 "tax refund, insurance settlement, property transaction or asset sale. Where "
                 "the counterparty identifies such a source, the alert should be cleared."),
    },
    {
        "policy_id": "AML-003",
        "title": "Sanctions and Watchlist Screening",
        "text": ("All wire counterparties must be screened against the current sanctions "
                 "watchlist. A positive match is a mandatory escalation with no analyst "
                 "discretion. The transaction must be held and compliance notified the same "
                 "business day. Partial name matches require manual adjudication but must "
                 "still be escalated."),
    },
    {
        "policy_id": "AML-004",
        "title": "Circular and Layering Patterns",
        "text": ("Funds that move between three or more related accounts and return to the "
                 "originating account within a 10-day window, with no apparent economic "
                 "purpose, indicate layering. Layering patterns must be escalated. The "
                 "absence of a commercial relationship between the accounts strengthens the "
                 "case for escalation."),
    },
    {
        "policy_id": "AML-005",
        "title": "Escalation Standard and Documentation",
        "text": ("An escalation decision must cite the specific policy relied upon and the "
                 "specific transactions or account facts supporting it. A decision to clear "
                 "an alert must likewise record the evidence relied upon. Conclusions that "
                 "cannot be traced to a retrieved record are not acceptable for audit."),
    },
]

WATCHLIST = [
    {"name": "VOSTOK TRADING OOO", "listed": "2025-03-11", "program": "OFAC-SDN"},
    {"name": "ARDEN MARITIME SA", "listed": "2024-11-02", "program": "OFAC-SDN"},
    {"name": "NORTHGATE PETROCHEM", "listed": "2025-07-19", "program": "EU-CONSOLIDATED"},
    {"name": "SILVERBACK LOGISTICS FZE", "listed": "2026-01-08", "program": "OFAC-SDN"},
]


def main():
    customers = make_customers()
    txns, planted = make_transactions(customers)

    (OUT / "customers.json").write_text(json.dumps(customers, indent=2))
    (OUT / "transactions.json").write_text(json.dumps(txns, indent=2))
    (OUT / "policies.json").write_text(json.dumps(POLICIES, indent=2))
    (OUT / "watchlist.json").write_text(json.dumps(WATCHLIST, indent=2))
    (OUT / "planted.json").write_text(json.dumps(planted, indent=2))

    print(f"customers:    {len(customers)}")
    print(f"transactions: {len(txns)}")
    print(f"policies:     {len(POLICIES)}")
    print(f"watchlist:    {len(WATCHLIST)}")
    print(f"planted alerts: {len(planted)}")
    for k, v in sorted(planted.items(), key=lambda x: x[1]):
        print(f"  {k}  {v}")


if __name__ == "__main__":
    main()
