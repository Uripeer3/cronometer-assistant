# Architecture decisions

## 001 — Independent application, reusable connector

Own the assistant workflow in this repository. Depend on the existing Cronometer client through an adapter and a pinned upstream revision. Create a narrow connector fork only if actual compatibility work requires it. Avoid a Git submodule or copied upstream source for the initial implementation.

## 002 — Prove the workflow with a simulated diary

The initial service exposes no real writes and receives no Cronometer credentials. Validate state, units and retry behavior with synthetic fixtures first. This is a working compatibility prototype, not a completed voice-logging product.

## 003 — Keep core rules independent of the voice host

The assistant interprets speech and images. The service persists state and enforces quantities, source preferences, draft revisions, incomplete nutrients, and diary-write status. A later switch of assistant or voice interface should not discard products, drafts, or recipe history.

## 004 — Preserve unknown nutrition and write uncertainty

Unknown is not zero. A known subtotal with missing foods is incomplete. A timeout after adding food is not proof of failure. Report these states explicitly and require reconciliation rather than inventing data or blindly repeating writes.
