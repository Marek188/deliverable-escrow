# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
DeliverableEscrow
==================

A reusable escrow primitive for "did the delivered work match the brief"
disputes — the canonical Agentic Commerce use case for GenLayer: a client
funds an engagement in GEN, a provider (human or agent) submits a URL to
the finished work, and validators judge the deliverable against a
natural-language brief before releasing or refunding the funds.

This is not a demo wrapper around a single LLM call. The contract:

  * Holds real value (GEN) in escrow and moves it based on the outcome.
  * Supports many concurrent engagements between many clients/providers.
  * Enforces a submission deadline with an anyone-can-call refund path,
    so funds can never be stuck waiting on an unresponsive provider.
  * Uses a *custom* leader/validator pair (`gl.vm.run_nondet_unsafe`)
    rather than a convenience wrapper, so validators independently
    re-fetch the deliverable and re-derive the ACCEPT/REJECT decision
    instead of merely checking that the leader's output "looks valid".
    Only the objective `decision` field is compared for strict
    agreement; the free-text `reasoning` field is stored but excluded
    from comparison, since two LLMs will never word it identically.

Funds can never be stranded, including by a broken oracle
-----------------------------------------------------------
Two escape hatches already exist for the pre-work stages: the client
can cancel before the provider submits, and anyone can reclaim a refund
if the provider misses the submission deadline. But there was a third
gap: once a deliverable is submitted, resolution depends on
`resolve_engagement` actually succeeding — a dead `deliverable_url`, or
a persistent web-fetch/consensus failure, would leave funds stuck in
"Submitted" forever with no way out. `claim_stale_refund` closes that
gap: if `RESOLUTION_WINDOW_HOURS` passes after submission with no
successful resolution, anyone can trigger a refund to the client, the
same "anyone can act, funds never depend on one party" pattern used
everywhere else in this contract.

Design rationale for the equivalence check
-------------------------------------------
GenLayer's own guidance warns against validators that only check that a
leader's output is well-formed (e.g. "decision is one of ACCEPT/REJECT
and reasoning is a non-empty string") — that's leader-output-only
validation, not consensus. `resolve_engagement` instead has every
validator re-fetch `deliverable_url` and re-run the same judgment
prompt, then compares only the decision field between leader and
validator (Partial Field Matching). This is the correct pattern for a
binary settlement decision: the outcome must match exactly, while the
supporting reasoning is allowed to vary in wording.

Deploy with no constructor arguments. See the accompanying README for
a full walkthrough (Studio steps, example calls, and test scenarios).
"""

from genlayer import *
from dataclasses import dataclass
import json
import typing
from datetime import datetime, timezone

RESOLUTION_WINDOW_HOURS = 72  # how long resolve_engagement gets before a stale refund can be claimed


@gl.evm.contract_interface
class _Payee:
    """Thin interface used to send GEN to a plain wallet address (EOA).

    Sending value to an EOA is an external message on GenLayer and goes
    through this same contract-interface mechanism used for EVM calls,
    even though the recipient has no code. See:
    https://docs.genlayer.com/developers/intelligent-contracts/features/value-transfers
    """

    class View:
        pass

    class Write:
        pass


@allow_storage
@dataclass
class Engagement:
    client: Address
    provider: Address
    amount: u256
    brief: str
    deliverable_url: str
    status: str  # "Funded" | "Submitted" | "Accepted" | "Rejected" | "Expired" | "Cancelled" | "Stale"
    reasoning: str
    deadline: u256  # unix seconds — submission must happen before this
    resolution_deadline: u256  # unix seconds — set on submission; see claim_stale_refund
    created_at: u256  # unix seconds


class DeliverableEscrow(gl.Contract):
    engagements: TreeMap[u256, Engagement]
    next_id: u256
    client_engagements: TreeMap[Address, DynArray[u256]]
    provider_engagements: TreeMap[Address, DynArray[u256]]

    def __init__(self):
        self.next_id = u256(0)

    # ------------------------------------------------------------------
    # Lifecycle: create -> submit -> resolve (or cancel / expire)
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def create_engagement(self, provider: str, brief: str, deadline_hours: int) -> u256:
        """Client funds a new engagement. `brief` is the natural-language
        acceptance criteria validators will judge the deliverable against
        (e.g. "A working REST API with GET /health and POST /items
        endpoints, deployed and publicly reachable at the submitted URL").
        """
        amount = gl.message.value
        if amount == u256(0):
            raise gl.vm.UserError("fund the engagement with some GEN")

        provider_addr = Address(provider)
        if provider_addr == gl.message.sender_address:
            raise gl.vm.UserError("client and provider must differ")
        if not brief.strip():
            raise gl.vm.UserError("brief must not be empty")
        if deadline_hours <= 0:
            raise gl.vm.UserError("deadline_hours must be positive")

        now = int(datetime.now(timezone.utc).timestamp())
        engagement_id = self.next_id
        self.next_id = self.next_id + u256(1)

        self.engagements[engagement_id] = Engagement(
            client=gl.message.sender_address,
            provider=provider_addr,
            amount=amount,
            brief=brief,
            deliverable_url="",
            status="Funded",
            reasoning="",
            deadline=u256(now + deadline_hours * 3600),
            resolution_deadline=u256(0),  # set for real once the provider submits
            created_at=u256(now),
        )

        self._track(self.client_engagements, gl.message.sender_address, engagement_id)
        self._track(self.provider_engagements, provider_addr, engagement_id)
        return engagement_id

    @gl.public.write
    def submit_deliverable(self, engagement_id: int, deliverable_url: str) -> None:
        """Provider submits the URL where the finished work can be
        reviewed. Must happen before the deadline."""
        eng = self._get(engagement_id)
        if gl.message.sender_address != eng.provider:
            raise gl.vm.UserError("only the provider can submit")
        if eng.status != "Funded":
            raise gl.vm.UserError(f"cannot submit in status {eng.status}")
        now = int(datetime.now(timezone.utc).timestamp())
        if now > int(eng.deadline):
            raise gl.vm.UserError("deadline has passed")
        if not deliverable_url.strip():
            raise gl.vm.UserError("deliverable_url must not be empty")

        eng.deliverable_url = deliverable_url
        eng.status = "Submitted"
        eng.resolution_deadline = u256(now + RESOLUTION_WINDOW_HOURS * 3600)

    @gl.public.write
    def resolve_engagement(self, engagement_id: int) -> None:
        """Validators judge the submitted deliverable against the brief
        and settle the escrow. This is the contract's core Intelligent
        Contract behaviour — see module docstring for the equivalence
        design."""
        eng = self._get(engagement_id)
        if eng.status != "Submitted":
            raise gl.vm.UserError(f"nothing to resolve in status {eng.status}")

        brief = eng.brief
        url = eng.deliverable_url

        def leader_fn():
            web_data = gl.nondet.web.get(url)
            content = web_data.body.decode("utf-8", errors="replace")[:8000]
            prompt = f"""
            You are adjudicating whether a delivered piece of work satisfies
            an agreed brief, for the purpose of releasing an escrow payment.

            Brief (what was promised):
            {brief}

            Content fetched from the submitted deliverable URL:
            {content}

            Decide ACCEPT if the content substantively satisfies the brief,
            or REJECT if it does not, is missing, or is clearly unrelated.
            Return strict JSON only, no prose outside the JSON:
            {{"decision": "ACCEPT" or "REJECT", "reasoning": "one or two sentence justification"}}
            """
            response = gl.nondet.exec_prompt(prompt)
            data = json.loads(response)
            if data.get("decision") not in ("ACCEPT", "REJECT"):
                raise gl.vm.UserError("[EXPECTED] leader produced an invalid decision")
            return data

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            # Independent re-derivation: re-fetch the same URL and re-run
            # the same judgment task. Only the objective `decision` field
            # is compared — `reasoning` is free text and will never match
            # word-for-word between two LLM runs.
            validator_data = leader_fn()
            return leader_result.calldata["decision"] == validator_data["decision"]

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        eng.reasoning = result["reasoning"]
        if result["decision"] == "ACCEPT":
            eng.status = "Accepted"
            self._pay(eng.provider, eng.amount)
        else:
            eng.status = "Rejected"
            self._pay(eng.client, eng.amount)

    @gl.public.write
    def claim_stale_refund(self, engagement_id: int) -> None:
        """If resolve_engagement can never succeed — a dead
        deliverable_url, or a persistent web-fetch/consensus failure —
        funds must not be stranded in "Submitted" forever. Callable by
        anyone, like the other refund paths, once
        RESOLUTION_WINDOW_HOURS has passed since submission with no
        successful resolution."""
        eng = self._get(engagement_id)
        if eng.status != "Submitted":
            raise gl.vm.UserError(f"cannot claim a stale refund in status {eng.status}")
        now = int(datetime.now(timezone.utc).timestamp())
        if now <= int(eng.resolution_deadline):
            raise gl.vm.UserError("resolution window has not expired yet")
        eng.status = "Stale"
        self._pay(eng.client, eng.amount)

    # ------------------------------------------------------------------
    # Escape hatches — funds must never get permanently stuck
    # ------------------------------------------------------------------

    @gl.public.write
    def cancel_before_submission(self, engagement_id: int) -> None:
        """Client can reclaim funds any time before the provider submits."""
        eng = self._get(engagement_id)
        if gl.message.sender_address != eng.client:
            raise gl.vm.UserError("only the client can cancel")
        if eng.status != "Funded":
            raise gl.vm.UserError(f"cannot cancel in status {eng.status}")
        eng.status = "Cancelled"
        self._pay(eng.client, eng.amount)

    @gl.public.write
    def claim_refund_after_deadline(self, engagement_id: int) -> None:
        """Anyone can trigger this once the deadline passes with no
        submission, so a client is never dependent on a specific caller
        to get an unresponsive provider's escrow back."""
        eng = self._get(engagement_id)
        if eng.status != "Funded":
            raise gl.vm.UserError(f"cannot expire in status {eng.status}")
        now = int(datetime.now(timezone.utc).timestamp())
        if now <= int(eng.deadline):
            raise gl.vm.UserError("deadline has not passed yet")
        eng.status = "Expired"
        self._pay(eng.client, eng.amount)

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_engagement(self, engagement_id: int) -> TreeMap[str, typing.Any]:
        return self._get(engagement_id)

    @gl.public.view
    def get_engagement_count(self) -> u256:
        return self.next_id

    @gl.public.view
    def list_client_engagements(self, client: str) -> DynArray[u256]:
        return self.client_engagements.get(Address(client), DynArray[u256]())

    @gl.public.view
    def list_provider_engagements(self, provider: str) -> DynArray[u256]:
        return self.provider_engagements.get(Address(provider), DynArray[u256]())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, engagement_id: int) -> Engagement:
        eid = u256(engagement_id)
        if eid not in self.engagements:
            raise gl.vm.UserError("unknown engagement_id")
        return self.engagements[eid]

    def _pay(self, to: Address, amount: u256) -> None:
        _Payee(to).emit_transfer(value=amount)

    def _track(self, index: TreeMap[Address, DynArray[u256]], key: Address, engagement_id: u256) -> None:
        if key not in index:
            index[key] = DynArray[u256]()
        index[key].append(engagement_id)
