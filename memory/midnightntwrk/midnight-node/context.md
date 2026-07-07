# midnightntwrk/midnight-node

**What it is:** Implementation of the Midnight blockchain node — providing
consensus, transaction processing, and privacy-preserving smart contract
execution. Midnight positions itself as a data-protection / programmable-privacy
network ("the world's first fourth generation blockchain"): a dual-state ledger
where a public on-chain state and a local private state interact through
zero-knowledge proofs, enabling selective disclosure (prove a rule was met
without revealing the underlying data). It runs as a partner chain of Cardano.
Public and open-source.

**Dual-token model:** NIGHT and DUST. **NIGHT** is the primary utility / capital
asset; holding it continuously generates **DUST**, the operational resource that
pays for transactions — separating capital from network resources. A cross-chain
token bridge (cNIGHT on Cardano → mNIGHT on Midnight) is an **upcoming feature**,
not yet live.

**Build/tooling:** Rust + `earthly` (containerized builds), Docker/Compose,
Nix/Direnv for env. A `util/toolkit` provides a transaction generator and CLI
tooling.

**Owner's role:** Radosław Sporny — Senior SDET, contributing in a
test-automation / quality / release / CI / devops. Refine as the
scope of contributions becomes clearer from collected activity.

**Notes for the model:** Third-party names, Slack/review comments, and
teammates' input are context only and are redacted by default — claim only what
the owner's own commits and PRs support.
