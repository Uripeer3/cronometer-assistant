# Backup and restore drill

Use local CLI commands, never send runtime files to GitHub or an assistant. A
backup contains one consistent application database and the referenced private
`evidence/` images. It excludes Cronometer sessions, tunnel keys, the transfer inbox,
unreferenced content files, and older manually maintained photo folders. Durable
host/Library references remain references; they are not downloaded by backup.
Import legacy photos into the managed archive before expecting them in a backup.

```bash
.venv/bin/python -m cronometer_assistant backup-runtime --db data/products.sqlite --backup-dir data/backups/pilot-01
.venv/bin/python -m cronometer_assistant verify-backup --backup-dir data/backups/pilot-01
.venv/bin/python -m cronometer_assistant restore-runtime --backup-dir data/backups/pilot-01 --restore-dir data/restores/pilot-01
```

Destination parents must be private (0700); new backup/restore directories are
created private and must not already exist. Files are 0600. Backups use SQLite's
online backup API, including committed WAL pages, under the application lock.
Referenced evidence hashes are checked; a final manifest marks completion. An
interrupted folder without a valid manifest is not a completed backup. Integrity
hashes detect damage, not malicious tampering or provide encryption.

Verification checks file hashes, SQLite integrity/schema and referenced evidence.
Restore validates before creating a new destination and never overwrites a working
database. The restored database is named `state.sqlite`, with `evidence/` beside it.
Launch a reviewed mode with `--db data/restores/pilot-01/state.sqlite` only after
inspection. Sessions/keys are separate: authenticate locally or use the existing
protected session explicitly, never restore credentials from this bundle.

Morning drill: restore into an isolated directory; verify draft IDs, usual-product
aliases, submission choice and archived photo hashes. Do not connect a second
writer to the same tunnel or use the restored data to repeat uncertain operations.
Back up each active database separately. Private permissions do not replace disk
encryption, a separate storage location, retention policy or tested recovery.
