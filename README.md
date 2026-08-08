# Settlement Feasibility & Fee Engine — Take-home

Welcome, and thanks for taking the time. The full problem is in
[`ASSIGNMENT.md`](./ASSIGNMENT.md). This README is just orientation.

## The task in one line

Given a client's escrow account, a settlement offer, and a creditor's rules,
decide whether the offer is affordable (and schedule it, collecting our fee as
early as allowed) or — if not — compute the minimum extra funding needed.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Layout

```
hiring_takehome/
├── ASSIGNMENT.md            # full specification — read this
├── feasibility/
│   ├── models.py            # data models, JSON loaders, date/EOM helpers (provided)
│   └── engine.py            # >>> implement evaluate_offer here <<< (+ Result shape)
├── cases/                   # four example cases (client.json / offer.json / creditor_rules.json)
│   ├── case1_feasible_even
│   ├── case2_infeasible_minima
│   ├── case3_balloon
│   └── case4_tiers
├── tests/
│   ├── test_smoke.py        # scaffolding sanity tests (pass out of the box)
│   └── test_cases.py        # example expectations — make these pass, then add your own
├── run.py                   # python run.py cases/<case>
└── requirements.txt
```

## Run

```bash
# evaluate a single case (prints the Result as JSON)
python run.py cases/case1_feasible_even

# tests
pytest -q
```

Out of the box, `tests/test_smoke.py` passes and `tests/test_cases.py` fails —
the latter is your target. Go beyond those four cases with your own tests.

## What to submit

Your implementation, your tests, and a short README section describing:
- your approach and the alternatives you considered,
- **your interpretation of the payment shapes** (even / staircase / balloon — we
  left these loosely defined on purpose),
- assumptions you made, and known edge cases / limitations.

Budget ~5–6 hours. Prefer a correct, well-tested core over breadth. When in
doubt, write down your assumption and keep going.


## Implementation Notes & Methodology

### Approach and Alternatives Considered
To ensure maintainability and strict adherence to the constraints, I separated the core logic into three distinct components:
1. **The Shape Generator:** A mathematical engine that evaluates rules (tiers, token pays) independently of the client's balance to generate ideal payment shapes.
2. **The Ledger Simulator:** A chronological simulation engine that enforces same-day ordering, calculates exact balances, and greedily extracts the program fee.
3. **The Gap Calculator:** Rather than building complex reverse-algebra to find missing funds, I utilized a Binary Search over the simulation engine. This guarantees finding the exact minimum penny required while naturally respecting all complex temporal constraints (like EOM shifting and fee timings).

*Alternative considered:* I initially considered mathematically calculating the exact extra funds from the point of failure backwards. However, because bank fees are contingent on creditor payments, and program fees are greedy, a binary search provided a much more robust and bug-free guarantee.

### Interpretation of Payment Shapes
The objective is to collect the program fee as early as possible. Therefore, the generator tests terms from `k = max_k` down to `1`, keeping early payments as mathematically low as possible. 

*   **Even:** The total is divided evenly by `k`. To strictly maintain the "non-decreasing" rule, any remainder cents are distributed exclusively to the final payments (e.g., `[8333, 8333, 8334]`).
*   **Balloon:** Every payment from `1` to `k-1` is set to the absolute minimum allowed by the token and tier rules. The entire remaining debt is dumped onto the `k`th payment.
*   **Staircase:** Since a balloon is essentially a 1-month step at the end, I interpreted a staircase as distributing the shortfall across the *last `N` payments* (where `N >= 2`). The algorithm iterates backwards, finding the steepest possible step at the end of the term that satisfies both the `max_segments` cap and the strict non-decreasing rule.

### Assumptions & Edge Cases Handled
*   **Rounding:** Python's native `round()` uses half-to-even (Banker's rounding). I implemented a custom `decimal`-based `round_half_up` helper to guarantee exact-cent matches for the financial tests.
*   **Horizon Bounds:** `max_payments` and `max_terms` are treated as a ceiling, but the ultimate bounds constraint is the `last_draft_date`. No `k` is tested that would push a cadence date past the horizon.
*   **Immutability:** When calculating the monthly increment via binary search, `LedgerEntry` immutability (`frozen=True`) is respected by deep-copying the client and generating fresh instances of future draft entries.