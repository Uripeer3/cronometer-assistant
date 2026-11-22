"""FastMCP server assembly for stdio and Streamable HTTP transports (ChatGPT, Claude, Gemini)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from cronometer_mcp import paging
from cronometer_mcp.client import CronometerError
from mcp.server.fastmcp import FastMCP

from . import __version__
from .auth import AuthError, SessionExpiredError
from .nutrition import ValidationError
from .service import CronometerAssistant
from .tools.core_tools import register_core_tools
from .tools.extras_tools import register_extras_tools
from .tools.tracking_tools import register_tracking_tools

logger = logging.getLogger(__name__)

# Keep idle HTTP sessions alive for 24h (FastMCP's 30m default drops idle web/mobile chats).
SESSION_IDLE_TIMEOUT = 24 * 3600

INSTRUCTIONS = """
You are a low-friction, accurate Cronometer nutrition logging assistant.
Match the user's language (or mix of languages).

Core workflow:
1. For "my usual" or repeated foods, check `get_my_foods` first (it returns saved aliases, recent foods, and custom foods in one call). Otherwise use `search_foods` (which also supports barcodes) and `get_food_details` for full nutrients.
2. Ask at most 1–2 practical clarifying questions only when genuinely impactful: e.g., raw/dry vs cooked weight for grains/pasta/meat, significant cooking oils/sauces, or ambiguous product variants.
3. Log directly with `log_food_entries` once you know what was eaten — pass `grams` OR `quantity` + `measure_id`/`measure_name` so the server computes exact grams from Cronometer's measure. Always include a unique `request_id` on the **first** write (and reuse that same `request_id` if you retry) so timeouts never cause duplicate entries.
4. **Same-meal additions — edit, don't duplicate:** If the user adds more of a food that is already logged in the same meal on that day (e.g., 100 g is already in dinner and they ask to add another 25 g to that meal), update the existing entry's total with `edit_food_entry` (100 g → 125 g) instead of calling `log_food_entries` to add another line — unless the user explicitly asks for a separate line or it is a distinct eating occasion (e.g., a separate snack later in the day).
5. For packaged products missing from Cronometer (including from nutrition-label photos you read), use `create_custom_food`. Always specify `carbohydrate_basis`: use `excludes_fiber` for EU/UK/AU/NZ/Israeli labels (and include `fiber`) and `includes_fiber` for US/Canadian labels. Call `list_nutrients` for micronutrients. Never fill unknown nutrients with 0.
6. Use `save_meal_draft` only when the user is still cooking and wants to save progress across chats; it is never required before logging.
7. Use `get_food_log` to answer "what did I eat today?" or "how many calories/protein do I have left?".
8. Never invent nutrient values, food IDs, or densities. Never ask for Cronometer passwords in chat; if a session expires, tell the user to run `cronometer-assistant login` locally.
""".strip()


def _ok(data: dict[str, Any]) -> str:
    return paging.fit({"ok": True, **data})


def _err(error: Exception) -> str:
    if isinstance(error, (SessionExpiredError, ValidationError, AuthError, CronometerError)):
        msg = str(error)
    elif isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code in (401, 403):
            msg = "Cronometer rejected the session. Run `cronometer-assistant login` locally."
        elif code == 429:
            msg = "Cronometer rate limit reached. Wait a moment and retry."
        else:
            msg = f"Cronometer HTTP error ({code})."
    elif isinstance(error, httpx.TimeoutException):
        msg = "Request to Cronometer timed out. Check `get_food_log` before retrying a write."
    elif isinstance(error, httpx.ConnectError):
        msg = "Could not connect to Cronometer. Check internet connectivity."
    else:
        logger.exception("Unexpected error in Cronometer MCP tool")
        msg = f"Unexpected error: {type(error).__name__}"
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def create_server(
    assistant: CronometerAssistant,
    *,
    include_extras: bool = True,
    include_tracking: bool = False,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> FastMCP:
    """Create the FastMCP server with cleanly separated tool tiers.

    - Core tools (always registered): 18 meal-logging / custom-food / recipe tools + paging.
    - Extras (`include_extras=True`): 10 diary-management, notes, targets, scores & streak tools.
    - Tracking (`include_tracking=False`): 13 opt-in biometric, exercise & fasting tools.
    """
    mcp = FastMCP(
        "cronometer-assistant",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        session_idle_timeout=SESSION_IDLE_TIMEOUT,
    )
    mcp._mcp_server.version = __version__
    paging.register(mcp)

    def run(fn, *args, **kwargs) -> str:
        try:
            return _ok(fn(*args, **kwargs))
        except Exception as error:
            return _err(error)

    register_core_tools(mcp, assistant, run)
    if include_extras:
        register_extras_tools(mcp, assistant, run)
    if include_tracking:
        register_tracking_tools(mcp, assistant, run)

    return mcp
