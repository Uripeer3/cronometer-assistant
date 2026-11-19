# Architecture decisions

## 001 — Independent application, reusable connector

Own the assistant workflow in this repository. Depend on the existing Cronometer client through an adapter and a pinned upstream revision. Create a narrow connector fork only if actual compatibility work requires it. Avoid a Git submodule or copied upstream source for the initial implementation.

## 002 — Prove the workflow with a simulated diary

The initial service exposes no real writes and receives no Cronometer credentials. Validate state, units and retry behavior with synthetic fixtures first. This is a working compatibility prototype, not a completed voice-logging product.

## 003 — Keep core rules independent of the voice host

The assistant interprets speech and images. The service persists state and enforces quantities, source preferences, draft revisions, incomplete nutrients, and diary-write status. A later switch of assistant or voice interface should not discard products, drafts, or recipe history.

## 004 — Preserve unknown nutrition and write uncertainty

Unknown is not zero. A known subtotal with missing foods is incomplete. A timeout after adding food is not proof of failure. Report these states explicitly and require reconciliation rather than inventing data or blindly repeating writes.

## 005 — Personal utility, minimal moving parts

One user, one machine, one Python service, SQLite, ordinary photo files and the
existing outbound tunnel. Keep simple nutrition checks and write/recovery records;
cut roadmap ceremony, repeated technical questions and mandatory public-submission
automation. Bring basic deployment forward after an early phone-tool check.
The complete feature map is a backlog, not 21 more PRs required for personal use.
See [personal scope and hosting](personal-scope.md) for the review and free-host limits.

A private session/optional credential file is sufficient; a separate secrets
service is not required. Current login remains interactive with session reuse.
Any optional password-file re-login needs an explicit implementation and does not
bypass two-factor authentication.

## 006 — Stabilize the personal meal flow before expanding parity

The 19 November review supersedes numeric delivery ordering in the older plan.
Fix concrete correctness/recovery findings, complete scoped live meal acceptance,
and provide one everyday mode before expanding native product/submission features.
Use requirement IDs in the roadmap; actual PR numbers are implementation history.
Do not import the entire upstream MCP tool surface just because the client exposes
it. Recent foods and read-only targets may reduce effort; exercise, fasting,
target writes and broad deletion remain outside the meal-logging mission.

One store and connection are the target, not a claim about today's separate
`products`/`meals` modes and databases. Preserve existing data through a backed-up
migration. Android hosting stays deferred; a host move is independent of laptop
meal correctness. See the [audit](code-review-2026-11-19.md) and
[capability comparison](upstream-comparison.md).
