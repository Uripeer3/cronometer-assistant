"""Run a deterministic demo or a local MCP server. Neither changes Cronometer."""

import argparse
import json
import tempfile
from pathlib import Path

from .service import MealService
from .store import Store


def run_demo(path: Path) -> dict:
    service = MealService(Store(path))
    meal = service.begin_meal("אורז ויוגורט / rice and yogurt", intent="eaten")["meal"]
    first = service.update_meal(
        meal["meal_id"], expected_revision=0,
        items=[{"description": "אורז / rice", "food_id": "demo:rice-cooked", "grams": 150}],
    )
    ready = service.update_meal(
        meal["meal_id"], expected_revision=1,
        items=[
            {"description": "אורז מבושל", "food_id": "demo:rice-cooked", "grams": 150,
             "preparation": "cooked"},
            {"description": "יוגורט", "food_id": "demo:yogurt", "grams": 200,
             "preparation": "as_sold"},
        ],
    )
    commit = service.commit_meal(meal["meal_id"], 2)
    repeated = service.commit_meal(meal["meal_id"], 2)
    return {
        "mode": "demo", "real_diary_changed": False,
        "first_follow_up": first["review"]["suggested_next_questions"],
        "review": ready["review"], "logging_state": commit["operation"]["state"],
        "repeat_was_deduplicated": repeated["already_logged"],
        "simulated_entries": len(service.status(meal["meal_id"])["diary"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "serve"])
    parser.add_argument("--db", type=Path, help="Persistent SQLite path (private runtime data)")
    args = parser.parse_args()
    if args.command == "demo":
        if args.db:
            print(json.dumps(run_demo(args.db), ensure_ascii=False, indent=2))
        else:
            with tempfile.TemporaryDirectory(prefix="meal-demo-") as folder:
                print(json.dumps(run_demo(Path(folder) / "demo.sqlite"),
                                 ensure_ascii=False, indent=2))
    else:
        from .server import create_server

        path = args.db or Path("data/demo.sqlite")
        create_server(MealService(Store(path))).run(transport="stdio")


if __name__ == "__main__":
    main()
