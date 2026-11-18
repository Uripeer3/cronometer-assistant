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

## Publication choice and manual outcomes

`set_product_submission_choice` persists requested/declined/deferred with an
expected revision. Retrieve `prepare_public_food_submission` to resume. The
assistant asks once, then follows the recorded choice; a new photo is not consent.
Declining preserves every evidence record. Changing choice after an actual
submission cannot retract what Cronometer already received.

`set_product_barcode` checks GTIN/EAN/UPC length and checksum without changing the
proposal's creation identity or claiming the barcode was linked in Cronometer.
The checklist re-reads the private food and checks archived front/label bytes.
A reference-only photo cannot satisfy local archive readiness. These checks do
not verify that images are legible or that a valid barcode identifies this variant.

`record_manual_submission_outcome` records an operator observation/receipt, always
attributed as operator_reported and independently_verified=false. It does not send
anything. Outcomes are submitted_pending, accepted, rejected or unknown. Do not
record a receipt based on a tool intention, file upload or inferred success.
Unknown blocks a fresh submission recommendation until the operator checks it.
Accepted/rejected require a preceding submission/unknown outcome; curation remains
Cronometer's decision. Do not place credentials or diary details in receipt text.

Morning test: request once, add a missing barcode later, restart and resume without
another approval. Exercise decline and defer separately. Simulate a lost response;
verify the result stays unknown and no upload or automatic repeat occurs.
