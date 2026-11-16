# Development workflow

## Architecture

| Module | Responsibility |
|---|---|
| `models.py` | Input validation, food/nutrient/item models, and explicit units |
| `nutrition.py` | Label normalization, incomplete nutrient summaries, portion arithmetic |
| `catalog.py` | Candidate ranking, synthetic catalog, and a read-only Cronometer adapter |
| `store.py` | SQLite documents and a host-local cross-process operation lock |
| `service.py` | Draft revisions, product memory, review, commit journal and reconciliation |
| `backend.py` | Diary boundary and the simulated implementation |
| `live.py` / `read_only_server.py` | Hidden local sign-in, private session, allowlisted HTTP, separate food-inspection MCP tools |
| `server.py` | Standard SDK tools and assistant instructions; local stdio |

The application is a single process, single user, and one writable SQLite database per instance. Use a local filesystem on one host; cross-host/network-filesystem locking and multi-user tenancy are not implemented. SQLite uses durable commits and a versioned schema. Its `fcntl` operation lock targets Linux/macOS.

## Commands

```bash
uv sync --locked --extra mcp --extra live --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked --extra mcp --extra live python -m unittest discover -s tests -v
uv run --locked cronometer-assistant demo
```

For the offline dependency-free core:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m cronometer_assistant demo
```

The MCP round-trip test skips when the SDK is absent locally. CI sets `REQUIRE_MCP_TESTS=1`, making absence a failure. CI installs the package and exercises a real stdio subprocess, not a substitute MCP implementation.

## Logging guarantees and limits

A meal ID identifies a single consumption event. Draft revisions prevent stale updates and commits. The journal is durable before each side effect, records returned entry IDs, and verifies the result by reading the diary. A second request for the same meal reuses its operation. An additional identical meal needs its own draft.

If an add times out, the write may have succeeded. The service does not guess from an identical food/weight alone and does not retry the addition. `reconcile_demo_entry` explicitly associates the attempt with a new matching diary entry. Once that is verified, the same operation can continue. A read-back failure with an already recorded remote ID can be retried without adding again.

The simulated backend deliberately has no upstream idempotency key. This exercises the actual uncertainty we expect from the private Cronometer API. Partial multi-food writes are retained in the journal; no transactional or exactly-once guarantee is asserted. Committed drafts are immutable in this release. Corrections, undo, and explicitly releasing an attempt proven not to have written anything are next-milestone features; never work around uncertainty by blindly creating another meal.

Only assistant-flagged or structurally known ambiguities are enforced by the backend. It does not infer missing oil or dressings from unrestricted speech. The host's dialogue/vision model supplies that interpretation; its pending questions remain in the draft until explicitly cleared.

## Dependency updates

The upstream connector is an optional dependency pinned to a commit. Our read-only adapter uses raw nutrient rows to preserve explicit missing amounts. Source numbers are unverified unless a mapping is supplied; never guess which numeric code means NCCDB.

Use an update PR to change the upstream commit or SDK version and regenerate `uv.lock`. Review protocol and authentication changes; run adapter fixtures and controlled live compatibility checks before deployment. If a connector patch is needed, maintain a narrow fork and point the pin to that patch. No changes have been sent upstream.

The committed `uv.lock` was resolved in GitHub Actions because the initial local environment could not reach PyPI. Normal CI checks the lockfile and source formatting without rewriting either. Regenerate the lockfile deliberately in dependency-update PRs.

## Runtime data and deployment

Runtime files are ignored by Git. The service creates its database with mode 0600 and new data directories with mode 0700. That does not replace disk encryption, authenticated remote access, or backup policy. Do not log raw tool requests, label photos, credentials, or sessions.

No production deployment, public MCP endpoint, scheduled task, real-account credential, or live Cronometer write is configured. Before deployment, add authentication, session expiry handling, database backup/restore checks, and a tested rollback path. Store runtime data on a persistent volume; deploy immutable tested revisions. The repository intentionally contains no environment secrets.

## Read-only account compatibility

See [setup](cronometer-read-only.md). CI installs the pinned live extra and sets
`REQUIRE_LIVE_TESTS=1`; real-client tests mock HTTP and use synthetic credentials.
No real-account tests or secrets run in CI. Read-only mode has a separate tool
surface and HTTP allowlist. The existing meal service still refuses real diary
backends. Source-code mappings and food/measure fidelity remain unverified.

Demo draft deletion is revision-checked and blocks any meal with a journal entry,
including uncertain writes. It removes only the local draft, never diary entries.
