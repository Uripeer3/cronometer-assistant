# Personal-use scope and simple hosting

Reviewed 17 November 2026. This is one person's home utility: easy meal logging,
short follow-up questions and straightforward fixes. It is not a commercial app.
The complete PR list is a feature checklist, not 21 more prerequisites to daily use.

## Review of the actual code

The feature stack contains about 3,200 lines of application Python, a pinned client
adapter, SQLite and ordinary files. The service does not invoke an LLM API itself;
the existing assistant does the conversation and image interpretation. There are
no separate database servers, queues, business accounts or distributed services.
The roadmap and user-facing workflow need more simplification than the core stack.

| Area | Keep | Simplify or postpone |
|---|---|---|
| Runtime | One Python application and the working outbound tunnel on one machine | One normal user-facing mode; demo/read-only modes remain development aids, not everyday setup choices |
| Data | One active SQLite database and one private photo directory | No managed Postgres, object store or separate sync service unless a chosen platform actually requires it |
| Writes | Exact IDs, short durable operation records, read-back and no blind duplicate writes | Group add/recovery/correction/undo work into a few useful changes; no enterprise workflow framework |
| Nutrition | Units, raw/cooked distinction, missing versus zero and remembered products | Validate adapter basis once against representative app entries; do not ask the user to certify every database row or expose category IDs in conversation |
| Photos | Original images associated with the product; retain them even after declining publication | Native Cronometer attachments and automatic CRDB submission can wait; manual Publish Food is an acceptable initial fallback |
| Submission | Remember requested/declined, plus a simple result when used | Automatic approval polling, notification infrastructure and elaborate curation lifecycle are optional |
| Repairs/deletion | Correct a logged amount and undo the associated entries | Rare custom-food administration can initially use the app and local reconciliation |
| Recipes | Ingredients, finished weight or fraction, and a saved version | Native recipe synchronization waits until its behavior is established and useful |
| Operations | Auto-start/restart, one backup command, readable scrubbed logs and a short update/revert recipe | No high availability, Kubernetes, monitoring dashboards, continuous deployment platform or bespoke migration framework |
| Tests | Focus on nutrition mistakes, duplicates, unintended deletion, restart and one restore drill | No release programme or mandatory test ceremony for every minor documentation change |

PR #12 currently requires explicit reviewed identity/preparation/basis and rejects
changed profiles. Keep the correctness boundary, but make the assistant perform
established checks and reuse verified matches. Only unresolved, consequential facts
should become questions to the user. Existing product metadata and snapshot tools
must not turn a normal meal into repeated approvals or technical data entry.

Do not strip durable journals/read-back just because this is personal use: duplicate
meals and damaged history would defeat the purpose. Those are small useful safeguards.

## Smaller delivery order

1. **Run the current tool away from the laptop.** Test one phone tool call, then
   install on an existing always-on home device or one small Linux VM. Keep the
   current tunnel, SQLite and file layout. One service starts it after reboot.
2. **Make ordinary logging useful.** Verify representative source/serving data,
   then implement diary read, add, uncertainty recovery, amount correction and
   exact-entry undo together as a coherent milestone. Use the journal already built.
3. **Make repeated products easy.** Usual-product aliases, label photos delivered
   automatically into the archive, serving grams and barcode reuse. Keep evidence
   even if publication is declined; use the app for submission until automation pays off.
4. **Add batch meals.** Save ingredients and yield; use weighed portions or accepted
   fractions. Preserve older versions when a recipe changes.
5. **Only then fill real friction gaps.** Native photo/submission automation, more
   custom-food administration or extra diagnostics are driven by actual use.

The numbered PRs 15–35 remain available for tracing requirements. Combine/split or
skip optional items rather than treating every number as a promised separate PR.
Move basic hosting (previous slot 33) early. Slot 34 means basic maintainability,
not a production operations project; slot 35 becomes a short personal-use trial.

## What hosting actually needs

The current implementation launches a long-running tunnel-client which launches
the stdio Python service. Calls arrive through its outbound connection, not a public
HTTP endpoint hosted by our Python application. Data/session/photo files must survive
restart. Therefore the direct fit is a continuously running Linux process with a
persistent disk. A provider's inbound-HTTP wake-on-request does not by itself wake
an already stopped outbound tunnel.

A small VM or existing Linux home device can keep this architecture. Use one service
manager, the existing private tunnel credentials and a persistent application folder.
There is no reason to add a public domain, reverse proxy, custom OAuth server or a
separate database merely to move the working tunnel to another machine.

### Options checked against provider documentation

These are published hosting allowances/prices checked 17 November 2026, not a
purchase or an account-specific availability guarantee. Taxes, optional services,
existing assistant subscriptions and any separate tunnel/API charges are outside
these host prices; no claim about tunnel billing is made here.

| Option | Host cost | Fit for current code | Practical consequence |
|---|---|---|---|
| Existing always-on NAS, small PC or suitable Raspberry Pi | No new hosting subscription; power/hardware still cost | Direct fit after checking OS/architecture/resources | Simplest free-hosting route if a suitable device is already running; laptop can sleep, that device cannot |
| Oracle Always Free VM and disk | Eligible resources within free allowance can be $0 | Direct Linux VM/disk fit; AMD and Arm options are documented | Capacity may be unavailable and idle VMs may be reclaimed; our small workload could meet idle criteria. A free experiment, not a promise of low maintenance |
| DigitalOcean Basic VM | Published $4/month for 512 MiB/10 GiB; $6/month for 1 GiB/25 GiB | Direct fit in architecture; actual memory still needs a smoke test | The 1 GiB option gives more headroom. Optional backups/taxes may add cost. This is a simple paid fallback, not required |
| Render free web service | $0 within free limits | Not a direct fit | Sleeps after 15 minutes of no inbound traffic, has no persistent disk, and local SQLite/photos disappear across restarts; its free database expires after 30 days |
| Koyeb free instance | One free instance within its limits | Not a direct fit | Web service only, sleeps after one hour without traffic, and cannot attach a persistent volume |

Oracle's idle policy evaluates low CPU/network use over seven days (and memory on
A1). Do not simulate activity to evade it. Availability and reclaim recovery can
make "free" less convenient than a small predictable bill.

Render/Koyeb could be made usable by replacing the outbound tunnel with authenticated
HTTP MCP, moving state/session storage outside ephemeral files, handling cold starts,
and checking host timeouts. That is additional integration work and accounts, not
just deleting a feature. Cloudflare/serverless is likewise a separate runtime/storage
port, not a drop-in home for this subprocess/fcntl/SQLite implementation. We should
not rewrite a working personal tool just to chase a free badge without measuring
whether it saves effort.

Dropping **photo retention alone** does not fix those platforms: drafts, usual products,
write journals and the session also need persistence, and the tunnel still needs
runtime. Dropping automated public submission reduces development work but does not
remove the hosting constraint. A free VM/home device does not require dropping those
features. Keep originals as requested unless the user changes that preference.

## GitHub or GitLab hosting, with or without photos

GitHub Pages and GitLab Pages publish static files. They do not run this Python
backend or its outbound tunnel. Moving the repository from GitHub to GitLab does
not change that. CI/CD can build, test and deploy the service somewhere else, but
is not a persistent application host; GitHub-hosted Actions jobs have a six-hour
execution limit. Chaining jobs to impersonate a server adds interruption and state
problems rather than making a personal tool easier to fix.

Photo handling has three independent parts: the assistant reading an attached
label, our server retaining the original, and uploading it to Cronometer/public
review. Removing the latter two cuts files, transfer code and submission machinery;
label reading and nutrient extraction can still work. We have not removed the
requested photo-retention behavior or discarded existing images.

Even with no archived photos, the current implementation needs a runtime, login
session and small persistent state for drafts, usual products and write recovery.
A static web page cannot serve as an authenticated remote MCP backend. A public
Pages site is also not a place for account credentials. We could deliberately
redesign a smaller HTTP/serverless backend with external free storage and cold
starts, but that is a hosting/runtime/authentication change, not a consequence of
switching Git providers. Prefer the existing small architecture unless $0 recurring
hosting is worth that extra work.

Sources: [GitHub Pages server-side limitation](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site),
[GitLab Pages static hosting](https://docs.gitlab.com/user/project/pages/), and
[GitHub Actions execution limits](https://docs.github.com/en/actions/reference/limits).

## Secret file: supported idea, not a reason for a bigger system

There is no inherent prohibition on the private server reading a secret file. The
language model does not need the username/password in its context. The service can
read a user-owned file or host secret directly without returning its contents through
MCP. Do not put it in Git or chat.

The current code already offers a simpler default: login once in the machine's
terminal and retain a private session file. No password is saved, and ordinary calls
reuse the session. On a VM, that terminal is an SSH session. A secret file does not
replace the need for a running service.

An optional credential-file login/re-login path is possible if repeated expiry
becomes inconvenient. It is **not implemented now**: live.py deliberately allows only
interactive login. Such a change should be opt-in, bounded and keep errors/logs free
of credentials. Two-factor authentication may still require the current code; a saved
password does not bypass it. Do not introduce a secret-manager service just for this.

## Recommendation

Keep the current small architecture and bring deployment forward. First check for
an existing suitable home device. If none exists, an Oracle Always Free VM is a
reasonable $0 experiment when accepting its capacity/reclaim caveats; use a small
paid VM if recovering a free instance becomes more trouble than the saving. Do not
open accounts, commit to charges or deploy before choosing the actual target.

### Sources

- [Render free services](https://render.com/docs/free)
- [Koyeb instance/free limits](https://www.koyeb.com/docs/reference/instances)
- [Oracle Always Free resources and reclamation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle Free Tier FAQ and capacity](https://www.oracle.com/cloud/free/faq/)
- [DigitalOcean Basic VM pricing](https://www.digitalocean.com/pricing/droplets)

The hosting fit, simplification decisions and credential-file discussion are our
engineering assessment of this repository, not promises made by those providers.
