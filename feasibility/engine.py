"""Candidate implementation goes here.

Implement ``evaluate_offer`` so that it satisfies the rules in ASSIGNMENT.md and
the example expectations in tests/test_cases.py. The dataclasses below define the
required OUTPUT shape (see ASSIGNMENT.md "Output"). You may add helpers, modules,
or rewrite internals freely, but keep ``evaluate_offer``'s signature and the
serialized shape of ``Result`` (so the runner and tests work).
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import date

from feasibility.models import (
    Client, 
    CreditorRules, 
    Offer,
    default_first_payment_date,
    monthly_payment_dates,
    offer_total_cents,
    program_fee_cents,
    round_half_up
)




@dataclass
class ScheduleRow:
    date: date
    creditor_payment_cents: int
    program_fee_cents: int
    bank_fee_cents: int
    balance_cents: int


@dataclass
class FundsOption:
    amount_cents: int
    within_guardrail: bool
    reason: str
    # lump-sum only:
    date: date | None = None
    # monthly-increment only:
    num_drafts: int | None = None


@dataclass
class AdditionalFunds:
    lump_sum: FundsOption
    monthly_increment: FundsOption


@dataclass
class Result:
    feasible: bool
    # One of "even", "staircase", or "balloon" — the shape your solution produced
    # (driven by the creditor flags). None when infeasible.
    pay_shape_used: str | None = None
    schedule: list[ScheduleRow] | None = None
    additional_funds: AdditionalFunds | None = None

    def to_dict(self) -> dict:
        out: dict = {"feasible": self.feasible, "pay_shape_used": self.pay_shape_used}
        out["schedule"] = (
            [
                {
                    "date": r.date.isoformat(),
                    "creditor_payment_cents": r.creditor_payment_cents,
                    "program_fee_cents": r.program_fee_cents,
                    "bank_fee_cents": r.bank_fee_cents,
                    "balance_cents": r.balance_cents,
                }
                for r in self.schedule
            ]
            if self.schedule is not None
            else None
        )
        if self.additional_funds is None:
            out["additional_funds"] = None
        else:
            def opt(o: FundsOption) -> dict:
                d = {
                    "amount_cents": o.amount_cents,
                    "within_guardrail": o.within_guardrail,
                    "reason": o.reason,
                }
                if o.date is not None:
                    d["date"] = o.date.isoformat()
                if o.num_drafts is not None:
                    d["num_drafts"] = o.num_drafts
                return d

            out["additional_funds"] = {
                "lump_sum": opt(self.additional_funds.lump_sum),
                "monthly_increment": opt(self.additional_funds.monthly_increment),
            }
        return out





# ---------------------------------------------------------------------------
# 1. Payment Generator Engine
# ---------------------------------------------------------------------------

def get_floor(idx_1b: int, rules: CreditorRules) -> int:
    """Calculates the absolute minimum payment allowed at a given position."""
    floor = rules.min_payment_cents
    
    # Token pays: after max_token_pays, payment must strictly exceed the base
    if idx_1b > rules.max_token_pays:
        floor = rules.min_payment_cents + 1
        
    # Tiers: apply any step-up minimums
    tier_max = 0
    for start_idx, min_cents in rules.min_payment_tiers:
        if idx_1b >= start_idx:
            tier_max = max(tier_max, min_cents)
            
    return max(floor, tier_max)

def generate_creditor_payments(
    offer_total: int, k: int, rules: CreditorRules
) -> tuple[str, list[int]] | None:
    """Finds a valid sequence of creditor payments for exactly 'k' months."""
    floors = [get_floor(i, rules) for i in range(1, k + 1)]
    
    if sum(floors) > offer_total:
        return None  # Impossible: the minimums alone exceed the debt
        
    # --- SHAPE: EVEN ---
    if rules.even_pays:
        base_pay = offer_total // k
        rem = offer_total % k
        
        if base_pay < max(floors):
            return None
            
        # Distribute remainder cents to the latest payments to stay non-decreasing
        payments = [base_pay] * (k - rem) + [base_pay + 1] * rem
        return "even", payments
        
    # --- SHAPE: BALLOON ---
    if rules.is_ballooning_allowed:
        payments = list(floors)
        payments[-1] += offer_total - sum(payments)
        return "balloon", payments
        
    # --- SHAPE: STAIRCASE ---
    # Try to spread the shortfall across the last N payments to create a step.
    for step_start_idx in range(max(0, k - 2), -1, -1):
        payments = list(floors)
        shortfall = offer_total - sum(payments)
        
        num_to_raise = k - step_start_idx
        if num_to_raise == 0:
            continue
            
        base_raise = shortfall // num_to_raise
        rem = shortfall % num_to_raise
        
        for i in range(step_start_idx, k):
            payments[i] += base_raise
        for i in range(k - rem, k):
            payments[i] += 1
            
        # Validate Staircase Constraints
        is_non_decreasing = all(payments[i] <= payments[i+1] for i in range(k - 1))
        if is_non_decreasing and len(set(payments)) <= rules.max_segments:
            return "staircase", payments
            
    return None


# ---------------------------------------------------------------------------
# 2. Ledger Simulator Engine
# ---------------------------------------------------------------------------

def get_all_cadence_dates(client: Client, offer: Offer) -> list[date]:
    """Generates all possible monthly cadence dates up to the horizon."""
    start = offer.first_payment_date or default_first_payment_date(client)
    # Generate enough dates to hit the horizon (120 months is a safe upper bound)
    dates = monthly_payment_dates(start, 120)
    return [d for d in dates if d <= client.last_draft_date]

def simulate_schedule(
    client: Client, 
    offer: Offer, 
    rules: CreditorRules, 
    creditor_payments: list[int]
) -> list[ScheduleRow] | None:
    """
    Simulates the ledger chronologically. 
    Returns the valid schedule if feasible, or None if the balance goes negative.
    """
    cadence_dates = get_all_cadence_dates(client, offer)
    
    # Map out which cadence dates have creditor payments
    creditor_map = {
        cadence_dates[i]: creditor_payments[i] 
        for i in range(len(creditor_payments))
    }
    
    # Filter ledger for future events we actually need to simulate
    future_ledger = [entry for entry in client.ledger if entry.date > client.as_of_date]
    
    # Get every unique date where SOMETHING happens
    all_dates = set(entry.date for entry in future_ledger)
    all_dates.update(cadence_dates)
    sorted_dates = sorted(list(all_dates))
    
    balance = client.current_balance_cents
    remaining_fee = program_fee_cents(offer, rules)
    schedule = []
    
    for current_date in sorted_dates:
        # 1. Apply all credits first (Same-day ordering constraint)
        day_credits = sum(e.amount_cents for e in future_ledger if e.date == current_date and e.type == "credit")
        balance += day_credits
        
        # 2. Apply existing fixed debits (past commitments)
        day_debits = sum(e.amount_cents for e in future_ledger if e.date == current_date and e.type == "debit")
        balance -= day_debits
        
        if balance < 0:
            return None  # The baseline ledger alone is unaffordable
            
        # 3. Process our settlement obligations if it's a cadence date
        if current_date in cadence_dates:
            c_payment = creditor_map.get(current_date, 0)
            b_fee = rules.bank_fee_cents if c_payment > 0 else 0
            
            balance -= (c_payment + b_fee)
            
            if balance < 0:
                return None  # We cannot afford the creditor/bank fee on this day
                
            # 4. Greedily collect the program fee (front-loaded)
            p_fee = 0
            if remaining_fee > 0:
                p_fee = min(remaining_fee, balance)
                balance -= p_fee
                remaining_fee -= p_fee
                
            # 5. Record the action if we did anything
            if c_payment > 0 or p_fee > 0 or b_fee > 0:
                schedule.append(ScheduleRow(
                    date=current_date,
                    creditor_payment_cents=c_payment,
                    program_fee_cents=p_fee,
                    bank_fee_cents=b_fee,
                    balance_cents=balance
                ))
                
    # Final check: did we collect our entire fee by the horizon?
    if remaining_fee > 0:
        return None
        
    return schedule


# ---------------------------------------------------------------------------
# 3. Phase 3: Infeasible Gap Calculator
# ---------------------------------------------------------------------------

def find_best_schedule(client: Client, offer: Offer, rules: CreditorRules) -> tuple[str, list[ScheduleRow]] | None:
    """Wrapper that tries all valid 'k' lengths and returns the best shape and schedule."""
    total_to_pay = offer_total_cents(offer)
    max_k = min(rules.max_terms, rules.max_payments)
    
    cadence_dates = get_all_cadence_dates(client, offer)
    max_k = min(max_k, len(cadence_dates))
    
    for k in range(max_k, 0, -1):
        shape_result = generate_creditor_payments(total_to_pay, k, rules) 
        if not shape_result:
            continue
            
        shape_used, payments = shape_result
        schedule = simulate_schedule(client, offer, rules, payments)
        if schedule is not None:
            return shape_used, schedule
            
    return None

def calculate_additional_funds(
    client: Client, offer: Offer, rules: CreditorRules
) -> AdditionalFunds:
    """Calculates the exact minimum lump sum and monthly increment needed."""
    total_to_pay = offer_total_cents(offer)
    
    # 1. Binary Search for Minimum Lump Sum
    # Search between 1 cent and a massive safe upper bound ($5M)
    low, high = 1, 500_000_000
    best_L = None
    
    while low <= high:
        mid = (low + high) // 2
        # Clone the client and give them a lump sum on the very first day
        test_client = copy.deepcopy(client)
        test_client.current_balance_cents += mid
        
        if find_best_schedule(test_client, offer, rules) is not None:
            best_L = mid
            high = mid - 1  # It worked! Now try a smaller amount
        else:
            low = mid + 1   # Need more money
            
    lump_guardrail = round_half_up(0.65 * total_to_pay)
    is_lump_valid = best_L is not None and best_L <= lump_guardrail
    lump_sum_opt = FundsOption(
        amount_cents=best_L or 0,
        within_guardrail=is_lump_valid,
        reason="Exceeds 65% of offer total" if (best_L and best_L > lump_guardrail) else ("No valid shape exists" if not best_L else ""),
        date=client.as_of_date if best_L else None
    )

    # 2. Binary Search for Minimum Monthly Increment
    low, high = 1, 500_000_000
    best_X = None
    
    # Find all future drafts (credits after as_of_date)
    future_drafts = [e for e in client.ledger if e.date > client.as_of_date and e.type == "credit"]
    num_drafts = len(future_drafts)
    
    if num_drafts > 0:
        while low <= high:
            mid = (low + high) // 2
            test_client = copy.deepcopy(client)
            
            # Add the increment to every future draft by creating new ledger entries
            # (Because LedgerEntry is frozen=True, we cannot do e.amount_cents += mid)
            new_ledger = []
            for e in test_client.ledger:
                if e.date > test_client.as_of_date and e.type == "credit":
                    # Create a fresh instance of the entry with the updated amount
                    new_ledger.append(type(e)(date=e.date, amount_cents=e.amount_cents + mid, type=e.type))
                else:
                    new_ledger.append(e)
            test_client.ledger = new_ledger
                    
            if find_best_schedule(test_client, offer, rules) is not None:
                best_X = mid
                high = mid - 1
            else:
                low = mid + 1

    monthly_guardrail = max(10000, round_half_up(0.40 * client.draft_amount_cents))
    is_monthly_valid = best_X is not None and best_X <= monthly_guardrail
    monthly_opt = FundsOption(
        amount_cents=best_X or 0,
        within_guardrail=is_monthly_valid,
        reason="Exceeds 40% of draft or $100 max" if (best_X and best_X > monthly_guardrail) else ("No valid shape exists" if not best_X else ""),
        num_drafts=num_drafts if best_X else 0
    )

    return AdditionalFunds(lump_sum=lump_sum_opt, monthly_increment=monthly_opt)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

def evaluate_offer(client: Client, offer: Offer, rules: CreditorRules) -> Result:
    """Evaluate a single offer. See ASSIGNMENT.md for the full specification."""
    
    success_result = find_best_schedule(client, offer, rules)
    
    if success_result is not None:
        shape_used, schedule = success_result
        return Result(
            feasible=True,
            pay_shape_used=shape_used,
            schedule=schedule,
            additional_funds=None
        )
    else:
        # Infeasible: calculate the gaps
        funds = calculate_additional_funds(client, offer, rules)
        return Result(
            feasible=False,
            pay_shape_used=None,
            schedule=None,
            additional_funds=funds
        )


# # ---------------------------------------------------------------------------
# # Helpers & Core Logic
# # ---------------------------------------------------------------------------

# def generate_creditor_payments(offer_total: int, rules: CreditorRules) -> tuple[str, list[int]]:
#     """
#     Determines the ideal payment shape (Even, Balloon, or Staircase) 
#     and returns a tuple of (shape_name, list_of_payment_amounts_in_cents).
#     """
#     # TODO: Implement the logic to generate [p1, p2, ..., pk]
#     pass

# def simulate_schedule(
#     client: Client, 
#     offer: Offer, 
#     rules: CreditorRules, 
#     creditor_payments: list[int]
# ) -> list[ScheduleRow] | None:
#     """
#     Simulates the ledger day-by-day. Applies credits, creditor payments, bank fees, 
#     and greedily collects the program fee.
#     Returns the valid schedule if feasible, or None if the balance ever drops < 0.
#     """
#     # TODO: Implement the chronological ledger simulation
#     pass

# def calculate_additional_funds(
#     client: Client, 
#     offer: Offer, 
#     rules: CreditorRules, 
#     creditor_payments: list[int]
# ) -> AdditionalFunds:
#     """
#     Calculates the exact minimum lump sum and monthly increment required 
#     if the baseline simulation fails.
#     """
#     # TODO: Implement math to find minimum L (Lump) and X (Monthly)
#     pass


# # ---------------------------------------------------------------------------
# # Main Entrypoint
# # ---------------------------------------------------------------------------

# def evaluate_offer(client: Client, offer: Offer, rules: CreditorRules) -> Result:
#     """Evaluate a single offer. See ASSIGNMENT.md for the full specification."""
    
#     # 1. Setup Totals
#     total_to_pay = offer_total_cents(offer)
    
#     # 2. Generate the ideal sequence of creditor payments based on rules
#     shape_used, payments = generate_creditor_payments(total_to_pay, rules)
    
#     # 3. Attempt to run the simulation with the current client funds
#     schedule = simulate_schedule(client, offer, rules, payments)
    
#     # 4. Route to Feasible or Infeasible outputs
#     if schedule is not None:
#         return Result(
#             feasible=True,
#             pay_shape_used=shape_used,
#             schedule=schedule,
#             additional_funds=None
#         )
#     else:
#         # If it failed, calculate what they need to make it work
#         funds = calculate_additional_funds(client, offer, rules, payments)
#         return Result(
#             feasible=False,
#             pay_shape_used=None,
#             schedule=None,
#             additional_funds=funds
#         )