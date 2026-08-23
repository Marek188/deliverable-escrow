# Deliverable Escrow

A reusable Intelligent Contract for [GenLayer](https://docs.genlayer.com)'s
canonical **Agentic Commerce** dispute: *did the delivered work match the
brief?* A client funds an engagement in GEN, a provider (human or agent)
submits a URL to the finished work, and GenLayer's AI validators
independently judge the deliverable against a natural-language brief
before releasing or refunding the escrow.

```
deliverable-escrow/
├── contracts/
│   └── deliverable_escrow.py   # the Intelligent Contract
├── test/
│   └── test_deliverable_escrow.py   # gltest integration tests
├── deploy/
│   └── deploy.ts                # genlayer-js deploy script
├── config/
│   └── gltest.config.yaml       # test network configuration
├── requirements.txt              # Python: genlayer-test, genlayer-py, pytest
├── package.json                  # Node: genlayer-js (for deploy.ts)
└── LICENSE                       # MIT
```

## Why this project

The brief for this design is `contracts/deliverable_escrow.py`'s own
docstring, reproduced in short: most demo Intelligent Contracts store a
single LLM output and stop there. This one holds real value, supports
many concurrent client/provider engagements, guarantees funds can never
get permanently stuck (client-cancel before submission, anyone-can-call
refund after a missed deadline), and — most importantly — implements the
Equivalence Principle the way GenLayer's own docs recommend: a **custom**
leader/validator pair where every validator independently re-fetches the
deliverable and re-derives the ACCEPT/REJECT decision, rather than merely
checking that the leader's output looks well-formed.

Full design rationale, state layout, and the public method table are in
[`contracts/`](./contracts) — see the module docstring at the top of
`deliverable_escrow.py`.

## Quickstart

```bash
# Python side (contract + tests)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Node side (deploy script)
npm install
```

### Run against GenLayer Studio (recommended first)

1. Start Studio locally (`genlayer init` then `genlayer up`) or use the
   hosted Studio at studio.genlayer.com.
2. Run the integration tests:
   ```bash
   gltest test/ -v -s
   ```
3. Or deploy directly and poke at it by hand:
   ```bash
   genlayer network studionet
   genlayer deploy
   ```

### Deploy to testnet

```bash
genlayer network testnet_asimov
genlayer deploy
```

Fund your account first via the
[testnet faucet](https://testnet-faucet.genlayer.foundation/).

## Status

Written directly against the current GenLayer docs (equivalence
principle, value transfers, transaction context, collection types). The
Python files are syntax-valid, but this has not yet been executed
against a live GenVM from the environment that authored it — run the
test suite above before relying on it for real funds. Contributions and
issue reports welcome.

## License

MIT — see [LICENSE](./LICENSE).
