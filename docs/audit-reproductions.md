# Reproducing the 19 November review findings

These examples are **synthetic only**: temporary SQLite state, existing fake
product client and pinned Cronometer client with mocked HTTP. They do not read
credentials or modify a real account. Run from the repository root after
installing `--extra live --extra mcp`. Save the code below to a temporary file,
then run `PYTHONPATH=src .venv/bin/python /path/to/review_repros.py`.

The script demonstrates current defects; its printed success states are not
expected behavior for a corrected release. Convert each into focused regression
tests in the implementation PR. Findings and fixes are in the
[review](code-review-2026-11-19.md).

Expected current outputs:

```text
resume_initial_state waiting_for_diary writes 0
resume_after_retired_changed_food committed writes 1
partial_state writing verified_first verified
partial_undo_rejected Select an item from a verified, logged meal
malformed_score_accepted 0 {'amount': None, 'unit': None}
old_draft_selection_after_other_verification 1 88 Another selected variant
product_old_review_creates_category True 7
```

```python
import sys
from unittest.mock import Mock
sys.path.insert(0, 'tests')
from test_live_meals import LiveMealTests
from cronometer_assistant.diary_changes import DiaryChanges
from cronometer_assistant.daily_nutrition import daily_nutrition
from cronometer_assistant.models import ValidationError

case = LiveMealTests('test_writes_with_exact_measure_translation_day_group_and_time')
case.setUp()
try:
    key, token = case.draft()
    case.bad_diary = True
    result = case.meals.commit_meal(key, 1, review_token=token, confirmed=True)
    print('resume_initial_state', result['operation']['state'], 'writes',len(case.writes))
    case.bad_diary = False
    case.food = {**case.food, 'retired': True, 'nutrients':[{'id':208,'amount':999}]}
    result = case.meals.commit_meal(key, 1, review_token=token, confirmed=True)
    print('resume_after_retired_changed_food',result['operation']['state'],'writes',len(case.writes))
finally:
    case.doCleanups()

case = LiveMealTests('test_writes_with_exact_measure_translation_day_group_and_time')
case.setUp()
try:
    key, token = case.draft(items=2)
    case.timeout_after_add = True
    case.meals.commit_meal(key, 1, review_token=token, confirmed=True)
    result = case.meals.reconcile_entry(key,0,'crono-serving:100')
    print('partial_state',result['operation']['state'],'verified_first',result['operation']['steps'][0]['state'])
    try:
        DiaryChanges(case.meals).undo_item(key,0,150,confirmed=True)
    except ValidationError as e:
        print('partial_undo_rejected',str(e))
finally:
    case.doCleanups()

case = LiveMealTests('test_writes_with_exact_measure_translation_day_group_and_time')
case.setUp()
try:
    key, token = case.draft()
    client=case.client
    client.get_nutrition_scores = Mock(return_value={})
    result=daily_nutrition(client,'2026-11-08')
    print('malformed_score_accepted',result['tracked_count'],result['summary']['energy'])
finally:
    case.doCleanups()

case = LiveMealTests('test_writes_with_exact_measure_translation_day_group_and_time')
case.setUp()
try:
    key, token = case.draft()
    case.client.search_food = Mock(return_value=[{'id':42,'name':'Another selected variant','translationId':88}])
    case.meals.verify_food('crono:42','cooked',True,True,88,'another rice')
    result=case.meals.review_meal(key)
    print('old_draft_selection_after_other_verification',result['meal']['revision'],result['review']['servings_to_log'][0]['translation_id'],result['review']['servings_to_log'][0]['food_name'])
finally:
    case.doCleanups()

from tempfile import TemporaryDirectory
from pathlib import Path
from test_products import FakeClient,label_values
from cronometer_assistant.products import ProductService
from cronometer_assistant.store import Store
with TemporaryDirectory() as folder:
    service=ProductService(Store(Path(folder)/'state.sqlite'),FakeClient())
    args=dict(name='Review test cereal',basis_amount=100,basis_unit='g',nutrients=label_values(),carbohydrate_basis='excludes_fiber',serving_grams=42)
    first=service.prepare(**args,category_id=5)['product']
    service.prepare(**args,category_id=7)
    result=service.create(first['proposal_id'],True)
    print('product_old_review_creates_category',result['created'],service.client.foods[123]['category'])
```
