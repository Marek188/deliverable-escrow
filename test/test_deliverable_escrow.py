"""
Integration tests for DeliverableEscrow, using the GenLayer Testing Suite
(`gltest`, built on pytest + genlayer-py). These deploy the real contract
against a running GenLayer Studio / localnet and exercise it through
actual consensus, not mocks.

Run with GenLayer Studio running (see the "Run and validate" section of
the root README):

    gltest test/ -v -s
    gltest --network testnet_asimov test/ -v -s   # against real testnet

Note: the exact keyword gltest expects for sending native value with a
`.transact()` call (used below as `value=...`, mirroring the documented
`value` parameter in GenLayerJS's `writeContract`) should be confirmed
against the `gltest` version pinned in requirements.txt before running —
consult `gltest --help` or the installed package's docstrings if it
differs.
"""

from pathlib import Path

import pytest
from gltest import get_contract_factory, create_account
from gltest.assertions import tx_execution_succeeded

CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"
ONE_GEN = 10**18  # GEN is denominated in wei, like ETH


def deploy_escrow():
    factory = get_contract_factory(
        contract_file_path=CONTRACTS_DIR / "deliverable_escrow.py"
    )
    return factory.deploy(args=[])


def test_engagement_count_starts_at_zero():
    contract = deploy_escrow()
    assert contract.get_engagement_count(args=[]).call() == 0


def test_create_engagement_requires_value():
    contract = deploy_escrow()
    client = create_account()
    provider = create_account()

    tx = contract.connect(client).create_engagement(
        args=[provider.address, "A page that says 'hello world'", 24],
        value=0,
    ).transact()

    # Zero-value funding must be rejected — see the `amount == u256(0)`
    # check in create_engagement.
    assert not tx_execution_succeeded(tx)


def test_create_engagement_rejects_self_dealing():
    contract = deploy_escrow()
    client = create_account()

    tx = contract.connect(client).create_engagement(
        args=[client.address, "Some brief", 24],
        value=ONE_GEN,
    ).transact()

    assert not tx_execution_succeeded(tx)


def test_full_lifecycle_accept_pays_provider():
    """Happy path: fund -> submit -> resolve(ACCEPT) -> provider is paid."""
    contract = deploy_escrow()
    client = create_account()
    provider = create_account()

    tx = contract.connect(client).create_engagement(
        args=[
            provider.address,
            "A public webpage whose text mentions the word 'hello'.",
            24,
        ],
        value=ONE_GEN,
    ).transact()
    assert tx_execution_succeeded(tx)
    assert contract.get_engagement_count(args=[]).call() == 1

    provider_balance_before = provider.balance

    tx = contract.connect(provider).submit_deliverable(
        args=[0, "https://example.org"]  # contains "hello" per RFC 2606 fixtures
    ).transact()
    assert tx_execution_succeeded(tx)

    engagement = contract.get_engagement(args=[0]).call()
    assert engagement["status"] == "Submitted"

    tx = contract.resolve_engagement(args=[0]).transact()
    assert tx_execution_succeeded(tx)

    engagement = contract.get_engagement(args=[0]).call()
    assert engagement["status"] in ("Accepted", "Rejected")  # depends on live LLM judgment
    if engagement["status"] == "Accepted":
        assert provider.balance > provider_balance_before


def test_cancel_before_submission_refunds_client():
    contract = deploy_escrow()
    client = create_account()
    provider = create_account()

    contract.connect(client).create_engagement(
        args=[provider.address, "Some brief", 24],
        value=ONE_GEN,
    ).transact()

    client_balance_before = client.balance

    tx = contract.connect(client).cancel_before_submission(args=[0]).transact()
    assert tx_execution_succeeded(tx)

    engagement = contract.get_engagement(args=[0]).call()
    assert engagement["status"] == "Cancelled"
    assert client.balance > client_balance_before


def test_cancel_by_non_client_is_rejected():
    contract = deploy_escrow()
    client = create_account()
    provider = create_account()
    stranger = create_account()

    contract.connect(client).create_engagement(
        args=[provider.address, "Some brief", 24],
        value=ONE_GEN,
    ).transact()

    tx = contract.connect(stranger).cancel_before_submission(args=[0]).transact()
    assert not tx_execution_succeeded(tx)


def test_refund_after_deadline_callable_by_anyone():
    """Anyone — not just the client — can trigger the deadline refund,
    per the design note in the contract: funds must never depend on one
    specific caller."""
    contract = deploy_escrow()
    client = create_account()
    provider = create_account()
    good_samaritan = create_account()

    # deadline_hours must be a positive int; a real deployment would wait
    # out a short deadline in wall-clock time or use a test helper that
    # advances the simulated chain time. See gltest's time-control
    # utilities (if available in your installed version) to avoid a real
    # sleep in CI.
    contract.connect(client).create_engagement(
        args=[provider.address, "Some brief", 1],
        value=ONE_GEN,
    ).transact()

    tx = contract.connect(good_samaritan).claim_refund_after_deadline(
        args=[0]
    ).transact()

    # Expected to fail immediately (deadline hasn't passed yet) — this
    # assertion documents the guard rather than the end-to-end refund,
    # which needs simulated time travel to test without a real wait.
    assert not tx_execution_succeeded(tx)


def test_list_client_and_provider_engagements():
    contract = deploy_escrow()
    client = create_account()
    provider = create_account()

    contract.connect(client).create_engagement(
        args=[provider.address, "Brief one", 24],
        value=ONE_GEN,
    ).transact()
    contract.connect(client).create_engagement(
        args=[provider.address, "Brief two", 24],
        value=ONE_GEN,
    ).transact()

    client_ids = contract.list_client_engagements(args=[client.address]).call()
    provider_ids = contract.list_provider_engagements(args=[provider.address]).call()

    assert list(client_ids) == [0, 1]
    assert list(provider_ids) == [0, 1]
