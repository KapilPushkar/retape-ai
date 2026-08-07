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
