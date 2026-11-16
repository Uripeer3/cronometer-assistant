# Private product evidence

Product mode can retain opaque host references and archive original image bytes.
It never uploads photos to Cronometer or submits a food. A host reference is not
proof of local retention. The archive rechecks each stored SHA-256 on review.

To enable imports, create a dedicated directory with mode 0700 and pass
`--evidence-inbox /absolute/private/inbox` to `serve-products`. A trusted host must
transfer the actual attachment there first. `import_product_photo` accepts only
its filename and one role: package_front, nutrition_label, barcode or ingredients.
URLs, arbitrary paths, symlinks, non-regular files and images over 10 MiB are rejected.
JPEG/PNG/WebP signatures are checked; this is not full image decoding or label OCR.
The assistant must still inspect identity and legibility.

Archive bytes live beside the database in `evidence/`, named by content hash, with
private permissions. Metadata binds them to a proposal/version, later associated
with its Cronometer ID. Repeated attachment of identical bytes in the same role
is deduplicated; separate products/roles keep separate metadata. Originals in the
inbox are preserved. A crash can leave an unreferenced content file, never a false
successful metadata row. Back up both SQLite and the archive.

`attach_product_evidence_reference` accepts an existing Library file ID or opaque
host ID without downloading it. `get_product_evidence` distinguishes these from
verified archived bytes. It never returns server filesystem paths or credentials.
No automatic chat-attachment transfer is implemented by this PR. Do not claim a
photo was archived merely because it was present in a conversation.

Morning test: transfer synthetic or selected product images to the inbox, import
front and label, repeat the imports, restart, then review. Corrupt/remove a test
copy and verify it is reported unavailable. Decline public submission and verify
that evidence stays private and retained.
