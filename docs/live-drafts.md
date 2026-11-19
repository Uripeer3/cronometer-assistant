# Real-food local drafts

`serve-drafts` adds persistent meal drafts and aliases backed by real Cronometer
food reads. It has no simulated or real diary tools and cannot log consumption.
Product mode also exposes these draft tools in its existing connection. Refresh
the connection's tool schemas after deployment and use a new test conversation.

Run `python3 scripts/connect_demo.py --tunnel-id YOUR_TUNNEL_ID --mode drafts`
after installing/updating the reviewed branch. This uses `data/drafts.sqlite`;
product mode shares `products.sqlite` with separate draft document namespaces.
Neither reads demo meals nor creates invented catalog foods. Databases are bound
to one account. The default day comes from the authenticated account timezone;
a detected timezone change requires reconnecting, never an account-setting write.

1. Search/inspect real foods. Source codes stay unverified until established.
2. Call `verify_food_for_draft` only after reviewing exact identity, preparation
   and per-100-g basis against the app/label. Do not infer basis confirmation from
   a field name. Saved preparation is tied to that inspected profile; a changed
   profile requires review again. No synthetic micronutrients are filled in.
3. Begin/update/review a local draft. The complete item list and unanswered
   questions persist. Ask about dry/cooked weights, oils and meaningful portions.
   Estimates require user agreement and a recorded assumption. Preparing stays
   a draft; eaten means the user confirmed consumption but still causes no write.
4. `remember_product` stores only user-established usual products. Hebrew aliases
   survive restart. `list_meal_drafts` finds drafts to resume; deletion is revision
   checked and applies only to an unlogged local draft.

The review reports known nutrient subtotals and missing coverage. `draft_complete`
means questions and consumption intent are resolved; `ready_to_log` remains false
and `logging_supported` false. Never describe this as logged in Cronometer.
Food basis confirmation and existing source mappings are explicit pilot gates.
The live ranker can use a whole brand phrase in a food name when a separate brand
field is missing; this is a search aid, not proof of exact product identity. If a
brand filter returns no candidate, search the exact product name and inspect it.

Morning test: use one confirmed generic food and the private cereal, switch
Hebrew/English, resolve preparation/quantity, record an accepted estimate, restart
and resume. Compare a weighed portion against the app without logging it. Check
the reported timezone before the scoped diary-write test in `meals` mode. That
mode adds real writes; this guide describes draft/product mode boundaries.
