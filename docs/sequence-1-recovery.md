# Sequence 1: reviewed identity and bounded recovery

Food verification returns an immutable `selection_id`. Keep that reference on
meal items and aliases, alongside the canonical food ID. Translations and
preparations of one canonical food can coexist. When several selections exist,
omitting the reference fails instead of selecting the most recent variant.
Existing saved drafts/aliases are bound once to the stored legacy selection;
this cannot reconstruct identity already lost before upgrading. Check any
ambiguous old draft. Existing pending operations without full approved snapshots
fail closed and must have their unsent remainder cancelled and reviewed anew.

Before each pending serving is sent, the server compares the approved profile,
selected measure definition and account timezone with fresh reads. Completed
steps are not replanned. A mismatch/read failure preserves all journal records.
If the selection genuinely changed, confirm `cancel_unsent_meal_items`, then
build and review a new draft containing **only cancelled items**. Verified,
uncertain and awaiting-verification entries must not be copied into that retry.

Cancellation affects only pending or definitively not-applied steps. It does not
send a diary deletion. A verified owned entry can be corrected or undone even
while another item is uncertain. Repeated logging skips verified/cancelled steps;
completion with cancellations is distinct from committing the whole meal.

The adapter distinguishes its own pre-send failure and explicit authentication/
API rejection from transport ambiguity. A definitely rejected add is not silently
retried; it may be cancelled and re-reviewed. A definitely rejected correction
can be reconfirmed after the current serving is checked. Timeouts, malformed
success replies and generic server failures remain uncertain. Absence from one
read does not prove a delayed mutation will never arrive.

After logging starts, `get_meal_review` labels the original draft as historical
and returns `current_recorded` from the journal. It shows verified corrected
amounts, undone entries and unknown quantities during unresolved changes, with
last-verified/requested amounts separate. `get_logging_status` additionally reads
the owned entries from Cronometer. Journal summaries are not a claim that no one
has edited the entry in the app; changes still validate the exact remote target.

Custom-food preparation/review returns `review_token`. Pass it to
`create_verified_cronometer_food` with user confirmation. Category, label
convention, serving weight, nutrients, name and barcode metadata are bound to
that review. Changing the proposal invalidates old approval. Refresh the ChatGPT
tool schema after deploying; old clients without a token cannot create a food.

Daily nutrition uses the pinned client's score request shape while validating
the exact diary response used to choose servings. Missing/malformed diary or
score structures fail explicitly. A valid empty target list, unavailable nutrient,
and explicit zero stay distinct; missing unit metadata cannot become a known
quantity. No broad target/account settings writes were added.

Synthetic acceptance covers changed pending profiles/measure definitions/timezones,
partial cleanup with an uncertain second item and an unrelated manual entry,
restart/deduplication, corrected/undone summaries, rejected changes followed by
external edits, coexisting translations, raw score/diary failure, and stale
product-category approval. Live diary acceptance is sequence 2.
