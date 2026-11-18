"""Run demo tools or inspect a real Cronometer food catalog without diary writes."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .models import ValidationError
from .service import MealService
from .store import Store


def run_demo(path: Path) -> dict:
    service = MealService(Store(path))
    meal = service.begin_meal("אורז ויוגורט / rice and yogurt", intent="eaten")["meal"]
    first = service.update_meal(
        meal["meal_id"],
        expected_revision=0,
        items=[{"description": "אורז / rice", "food_id": "demo:rice-cooked", "grams": 150}],
    )
    ready = service.update_meal(
        meal["meal_id"],
        expected_revision=1,
        items=[
            {
                "description": "אורז מבושל",
                "food_id": "demo:rice-cooked",
                "grams": 150,
                "preparation": "cooked",
            },
            {
                "description": "יוגורט",
                "food_id": "demo:yogurt",
                "grams": 200,
                "preparation": "as_sold",
            },
        ],
    )
    commit = service.commit_meal(meal["meal_id"], 2)
    repeated = service.commit_meal(meal["meal_id"], 2)
    return {
        "mode": "demo",
        "real_diary_changed": False,
        "first_follow_up": first["review"]["suggested_next_questions"],
        "review": ready["review"],
        "logging_state": commit["operation"]["state"],
        "repeat_was_deduplicated": repeated["already_logged"],
        "simulated_entries": len(service.status(meal["meal_id"])["diary"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "demo",
            "serve",
            "login",
            "check-cronometer",
            "serve-read-only",
            "serve-products",
            "serve-drafts",
            "backup-runtime",
            "verify-backup",
            "restore-runtime",
        ],
    )
    parser.add_argument("--db", type=Path, help="Persistent SQLite path (private runtime data)")
    parser.add_argument(
        "--session",
        type=Path,
        default=Path("data/cronometer-session/session.json"),
        help="Private local Cronometer session; never a password file",
    )
    parser.add_argument("--evidence-inbox", type=Path, help="Private host-managed image inbox")
    parser.add_argument(
        "--backup-dir", type=Path, help="New private backup or existing verified backup"
    )
    parser.add_argument("--restore-dir", type=Path, help="New isolated restore directory")
    args = parser.parse_args()
    if args.command in {"backup-runtime", "verify-backup", "restore-runtime"}:
        from .backups import create_backup, restore_backup, verify_backup

        try:
            if args.backup_dir is None:
                raise ValidationError("Provide --backup-dir")
            if args.command == "backup-runtime":
                if args.db is None or args.db.is_symlink() or not args.db.is_file():
                    raise ValidationError(
                        "Provide --db pointing to an existing application database"
                    )
                result = create_backup(Store(args.db), args.backup_dir)
            elif args.command == "verify-backup":
                result = verify_backup(args.backup_dir)
            else:
                if args.restore_dir is None:
                    raise ValidationError("Provide a new --restore-dir")
                result = restore_backup(args.backup_dir, args.restore_dir)
            print(json.dumps(result, indent=2))
        except (OSError, ValueError, RuntimeError):
            print(
                "Backup operation failed; check private paths, integrity and destination availability.",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        return
    if args.command in {
        "login",
        "check-cronometer",
        "serve-read-only",
        "serve-products",
        "serve-drafts",
    }:
        from .live import LiveError, ReadOnlyConnection, create_client

        connection = None
        try:
            connection = ReadOnlyConnection(
                create_client(
                    args.session,
                    interactive=args.command == "login",
                    allow_product_creation=args.command == "serve-products",
                )
            )
            if args.command == "login":
                connection.client.login()
                print("Session saved privately. Run check-cronometer to verify food-data access.")
            elif args.command == "check-cronometer":
                print(json.dumps(connection.check(), ensure_ascii=False, indent=2))
            else:
                from .read_only_server import create_read_only_server

                products = None
                if args.command == "serve-products":
                    from .products import ProductService

                    products = ProductService(
                        Store(args.db or Path("data/products.sqlite")),
                        connection.client,
                        evidence_inbox=args.evidence_inbox,
                    )
                drafts = None
                if args.command in {"serve-drafts", "serve-products"}:
                    from .live_drafts import LiveDraftService

                    drafts = LiveDraftService(
                        Store(
                            args.db
                            or Path("data/products.sqlite" if products else "data/drafts.sqlite")
                        ),
                        connection.client,
                    )
                create_read_only_server(
                    connection, product_service=products, draft_service=drafts
                ).run(transport="stdio")
        except (LiveError, ValidationError) as error:
            print(str(error), file=sys.stderr)  # Only our scrubbed diagnostics.
            raise SystemExit(1) from None
        except (OSError, EOFError):
            print("Connection unavailable. Check setup and run login locally.", file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            if connection:
                connection.close()
        return
    if args.command == "demo":
        if args.db:
            print(json.dumps(run_demo(args.db), ensure_ascii=False, indent=2))
        else:
            with tempfile.TemporaryDirectory(prefix="meal-demo-") as folder:
                print(
                    json.dumps(run_demo(Path(folder) / "demo.sqlite"), ensure_ascii=False, indent=2)
                )
    else:
        from .server import create_server

        path = args.db or Path("data/demo.sqlite")
        create_server(MealService(Store(path))).run(transport="stdio")


if __name__ == "__main__":
    main()
