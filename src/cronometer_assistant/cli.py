"""CLI for Cronometer authentication, health checks, and running the MCP server."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .auth import AuthError, CronometerSessionClient
from .nutrition import ValidationError
from .service import CronometerAssistant
from .state import AssistantState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cronometer-assistant",
        description="Simple, thorough conversational MCP server for Cronometer.",
    )
    parser.add_argument(
        "command",
        choices=["login", "check", "serve"],
        help="Command to run.",
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=Path("data/cronometer-session/session.json"),
        help="Private mode-0600 Cronometer session file (never stores passwords).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/assistant.sqlite"),
        help="Local SQLite file for aliases, idempotency, and optional drafts.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport for `serve` (stdio for ChatGPT tunnel/Claude Code/Gemini CLI; streamable-http for remote Claude.ai/Gemini HTTP).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for streamable-http transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port for streamable-http transport (default: 8765).",
    )
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="Expose only the 18 core meal-logging tools (+ paging), omitting diary/target extras.",
    )
    parser.add_argument(
        "--include-tracking",
        action="store_true",
        help="Also expose biometrics, exercise, and fasting tools.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = build_parser().parse_args(argv)

    client: CronometerSessionClient | None = None
    try:
        client = CronometerSessionClient(args.session, interactive=(args.command == "login"))
        if args.command == "login":
            client.login()
            print("Session saved privately. Run `cronometer-assistant check` to verify access.")
        elif args.command == "check":
            assistant = CronometerAssistant(client)
            print(json.dumps(assistant.foods.check_connection(), ensure_ascii=False, indent=2))
        else:
            from .server import create_server

            state = AssistantState(args.db, user_id=client.user_id)
            assistant = CronometerAssistant(client, state=state)
            server = create_server(
                assistant,
                include_extras=not args.core_only,
                include_tracking=args.include_tracking,
                host=args.host,
                port=args.port,
            )
            server.run(transport=args.transport)
    except (AuthError, ValidationError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, EOFError) as error:
        print(f"Unable to start Cronometer assistant: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
