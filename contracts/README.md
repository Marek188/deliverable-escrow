# DeliverableEscrow

A reusable Intelligent Contract for the canonical **Agentic Commerce**
dispute GenLayer itself calls out: *"did the delivered work match the
brief?"* A client funds an engagement in GEN, a provider (human or
agent) submits a URL to the finished work, and GenLayer's validators
independently judge the deliverable against a natural-language brief
before releasing or refunding the escrow.

File: [`deliverable_escrow.py`](./deliverable_escrow.py)

## Why this isn't a thin LLM wrapper

The contribution guidelines explicitly rule out hello-world examples,
simple storage, and thin LLM wrappers. This contract instead:

- **Holds and moves real value.** `create_engagement` is `payable`;
  `resolve_engagement` actually pays the provider or refunds the client
  based on the validated outcome — not just a stored string.
- **Supports many concurrent engagements**, indexed by id and by both
  client and provider address (`TreeMap` + `DynArray`), so it's usable
  as a real marketplace primitive, not a single-use demo.
- **Never lets funds get stuck.** `cancel_before_submission` lets the
  client walk away before work starts; `claim_refund_after_deadline` is
  callable by *anyone* once a provider goes unresponsive past the
  deadline; and `claim_stale_refund` is callable by anyone if
  `resolve_engagement` itself can never succeed — a dead
  `deliverable_url`, or a persistent web-fetch/consensus failure — so a
  broken oracle can't strand escrowed funds in "Submitted" forever.
- **Uses a real, custom equivalence check**, not a convenience wrapper
  — see below.

## The equivalence principle, done the recommended way

GenLayer's own docs warn against validators that only check the
*shape* of a leader's output (`decision in ("ACCEPT","REJECT")` and
`reasoning` non-empty) — that's leader-output-only validation, not
consensus, because it never verifies the decision actually follows
from the source data.

`resolve_engagement` instead uses a custom `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`
pair (**Partial Field Matching**, per
[the Equivalence Principle guide](https://docs.genlayer.com/developers/intelligent-contracts/equivalence-principle#pattern-1-partial-field-matching)):

- `leader_fn` fetches the deliverable URL and asks an LLM to return
  strict JSON: `{"decision": "ACCEPT"|"REJECT", "reasoning": "..."}`.
- `validator_fn` **re-fetches the same URL and re-runs the exact same
  judgment task independently**, then compares only the `decision`
  field between leader and validator for exact equality.
- `reasoning` is stored (so the outcome is explainable) but **excluded**
  from the equivalence check — two independent LLM calls will word
  their justification differently even when they agree on the verdict,
  and requiring exact text match there would make consensus
  practically unreachable.

This mirrors the pattern GenLayer's docs use for the football-result
and quality-scoring examples: separate the **objective decision field**
(must match exactly) from the **subjective analysis field** (stored,
not compared).

## State design

```python
Engagement:
  client, provider: Address
  amount: u256                # escrowed GEN
  brief: str                  # natural-language acceptance criteria
  deliverable_url: str
  status: str                 # Funded → Submitted → Accepted | Rejected
                               #        ↘ Cancelled / Expired
  reasoning: str               # validator-accepted justification
  deadline, created_at: u256   # unix seconds, from tx timestamp
```

`client_engagements` / `provider_engagements` are `TreeMap[Address, DynArray[u256]]`
indexes so a frontend can list "my engagements" without an off-chain indexer.

## Public interface

| Method | Caller | Effect |
|---|---|---|
| `create_engagement(provider, brief, deadline_hours)` *(payable)* | client | funds a new engagement, returns its id |
| `submit_deliverable(id, deliverable_url)` | provider | records the submission before the deadline |
| `resolve_engagement(id)` | anyone | triggers validator judgment; pays provider or refunds client |
| `cancel_before_submission(id)` | client | refund before provider submits |
| `claim_refund_after_deadline(id)` | anyone | refund once deadline passes with no submission |
| `claim_stale_refund(id)` | anyone | refund once the resolution window passes with no successful `resolve_engagement` |
| `get_engagement(id)` | view | full engagement record |
| `get_engagement_count()` | view | total engagements created |
| `list_client_engagements(addr)` / `list_provider_engagements(addr)` | view | ids for a given address |

## Testing in GenLayer Studio

1. Open [GenLayer Studio](https://studio.genlayer.com) (or run it
   locally per the [tooling setup guide](https://docs.genlayer.com/developers/intelligent-contracts/tooling-setup)).
2. Load `deliverable_escrow.py` and deploy — no constructor args needed.
3. **Happy path:** call `create_engagement` from Account A with some
   GEN value, `provider` = Account B's address, a concrete `brief`
   (e.g. *"A public webpage that lists at least three animals"*), and
   `deadline_hours = 24`. Switch to Account B, call
   `submit_deliverable(0, "<a real URL that satisfies the brief>")`.
   Call `resolve_engagement(0)` and confirm status becomes `Accepted`
   and B's balance increases by the escrowed amount.
4. **Rejection path:** repeat with a `deliverable_url` that clearly
   doesn't satisfy the brief (e.g. an unrelated page) and confirm
   `resolve_engagement` returns funds to the client instead.
5. **Expiry path:** create an engagement with `deadline_hours = 0`
   won't pass validation (must be positive) — use a short deadline,
   let it pass, then call `claim_refund_after_deadline` from a *third*
   account to confirm the refund doesn't depend on the client calling
   it themselves.
6. Check the Studio's Run & Debug log for the `resolve_engagement`
   transaction — you'll see the leader's proposed `{decision, reasoning}`
   and each validator's independent agreement/disagreement.

## Notes for reviewers

This file was written directly against the current GenLayer docs
(equivalence principle, value transfers, transaction context, and
collection types pages) and is syntax-valid Python, but has **not**
been executed inside GenVM/Studio from this environment — please run
the scenarios above before merging or shipping funds against it.
