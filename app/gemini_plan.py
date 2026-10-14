"""Read a deep research plan back from its saved chat.

Gemini sends a plan on the live stream, but the stream can be cut before the plan
arrives. Gemini still finishes the planning turn on its side, so the plan is in the
saved chat. The library's chat reader keeps the research report but drops the plan,
so this module reads the same turns and keeps the plan.

It relies on two library internals: `_batch_execute` and the conversation-turns RPC
id. Both belong to the pinned gemini-webapi version in requirements.txt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from gemini_webapi.constants import GRPC
from gemini_webapi.types import DeepResearchPlan, RPCData
from gemini_webapi.utils import extract_json_from_response, get_nested_value
from gemini_webapi.utils.research import extract_deep_research_plan

log = logging.getLogger("gemini_webui")


@dataclass(frozen=True)
class SavedPlan:
    plan: DeepResearchPlan
    rid: str  # the reply id of the turn that holds the plan
    rcid: str  # the candidate id of that reply


def _turns_payload(cid: str, limit: int) -> str:
    # The same compact JSON the library sends for its own chat reads.
    return json.dumps([cid, limit, None, 1, [1], [4], None, 1], separators=(",", ":"))


def turns_from_response(text: str) -> list:
    """The turns from a conversation-turns response, newest first. Empty if none."""
    try:
        for part in extract_json_from_response(text):
            body_text = get_nested_value(part, [2])
            if not isinstance(body_text, str) or not body_text:
                continue
            turns = get_nested_value(json.loads(body_text), [0])
            if turns:
                return turns
    except (TypeError, ValueError) as exc:
        log.debug("saved chat response did not parse: %s", exc)
    return []


def find_saved_plan(turns: list) -> SavedPlan | None:
    """The newest plan in the turns, with the ids its confirmation needs."""
    for conv_turn in turns:
        rid = get_nested_value(conv_turn, [0, 1], "")
        for candidate in get_nested_value(conv_turn, [3, 0]) or []:
            if not isinstance(candidate, list):
                continue
            text = get_nested_value(candidate, [1, 0], "")
            found = extract_deep_research_plan(
                candidate, fallback_text=text if isinstance(text, str) else ""
            )
            if found is None:
                continue
            plan = DeepResearchPlan(**found)
            if not plan.confirm_prompt:
                plan.confirm_prompt = "Start research"  # the library's own default
            rcid = get_nested_value(candidate, [0], "")
            return SavedPlan(
                plan=plan,
                rid=rid if isinstance(rid, str) else "",
                rcid=rcid if isinstance(rcid, str) else "",
            )
    return None


def describe_turns(turns: list) -> str:
    """A short description of the newest turn, for the log when no plan is found yet."""
    if not turns:
        return "no turns"
    candidates = get_nested_value(turns[0], [3, 0]) or []
    if not candidates or not isinstance(candidates[0], list):
        return f"{len(turns)} turns, newest has no candidates"
    first = candidates[0]
    rich = get_nested_value(first, [12])
    filled = [i for i, value in enumerate(rich) if value] if isinstance(rich, list) else []
    return (
        f"{len(turns)} turns, newest candidate status {get_nested_value(first, [8, 0])}, "
        f"rich fields set {filled[:20]}"
    )


async def read_saved_plan(client: Any, cid: str, limit: int = 10) -> SavedPlan | None:
    """The plan in the chat's latest turns, or None while Gemini has not written one."""
    response = await client._batch_execute(
        [RPCData(rpcid=GRPC.LIST_CONVERSATION_TURNS, payload=_turns_payload(cid, limit))],
        # By default the library closes the client on a failed call. The client is shared
        # by the other requests, so a failed read must leave it open.
        close_on_error=False,
    )
    turns = turns_from_response(response.text)
    saved = find_saved_plan(turns)
    if saved is None:
        # Logged as a warning: the app does not configure logging, so only warnings reach
        # the Vercel logs. This shows whether Gemini's turn is filling in, turn by turn.
        log.warning("saved chat has no plan yet: %s", describe_turns(turns))
    return saved
