# Review manual corrections to an existing private food

Reconcile a food corrected in Cronometer without deleting/recreating it. These
tools do not issue remote edits. They preserve the food ID and bring the local
creation journal up to date after reviewing the existing food in the app.

1. Use `prepare_product_repair` with the existing proposal, a verified category ID
   and serving grams. Review observed measures and target category/label type.
2. If needed, edit that same food in Cronometer. Check nutrition, serving Weight
   type, grams, category and label convention. Consider historical diary impact.
3. Prepare a fresh snapshot after the edits. Adopt only when it reports a match.
4. `adopt_manual_product_repair` with reviewed=true re-reads the same food, checks
   ownership/creation marker, rejects intervening changes, and verifies nutrients,
   default Weight serving and 1 g measure before updating local metadata.

The prior expectation and observed snapshot remain in a private repair journal.
A changed local proposal or remote record needs a fresh review. Read failures
leave the local expectation untouched. Reusing an adopted repair still rechecks
Cronometer. Later capturing a barcode for a matching known product reuses its
proposal rather than creating a duplicate. No barcode linkage, upload or diary
edit occurs. Nutrient/formulation changes need a new reviewed version.

Morning test: review the manually corrected pilot food, compare the app, adopt its
current category/serving, then confirm reuse causes no new food. In a fixture,
change a field between review and adopt and confirm it is rejected. Automated
field-scoped remote edits remain separate roadmap work.
