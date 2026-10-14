"""Reading a deep research plan from a saved chat. The RPC is faked; no network."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.gemini_plan import describe_turns, find_saved_plan, read_saved_plan, turns_from_response


def plan_payload() -> list:
    return [
        "Battery recycling plan",
        [[None, "Survey", "Read the sources"], [None, "Compare", "Weigh the findings"]],
        "about 8 minutes",
        ["Start research"],
        ["https://gemini.google.com/app/plan"],
        [["Change the plan"]],
    ]


def candidate(rcid: str, text: str, plan: list | None = None) -> list:
    rich = [None] * 70
    if plan is not None:
        rich[55] = plan  # Field.RESEARCH_PLAN
    found = [None] * 13
    found[0] = rcid
    found[1] = [text]
    found[8] = [2]  # finished
    found[12] = rich
    return found


def turn(rid: str, candidates: list) -> list:
    return [["c_saved", rid], None, [["Indian economy"]], [candidates]]


def wire(parts: list) -> str:
    """Frame JSON the way the batch endpoint does: each payload is preceded by its length."""
    body = json.dumps(parts)
    return ")]}'\n\n" + f"{len(body.encode('utf-16-le')) // 2}\n{body}\n"


class FakeBatchClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.payloads: list[str] = []
        self.close_on_error: list[bool] = []

    async def _batch_execute(self, payloads, close_on_error=True, **_):
        self.payloads.extend(p.payload for p in payloads)
        self.close_on_error.append(close_on_error)
        return SimpleNamespace(text=self.text)


def test_the_newest_plan_is_found_with_its_turn_ids():
    turns = [
        turn("r_new", [candidate("rc_new", "Here is my plan.", plan_payload())]),
        turn("r_old", [candidate("rc_old", "An earlier answer.")]),
    ]
    saved = find_saved_plan(turns)
    assert saved is not None
    assert (saved.rid, saved.rcid) == ("r_new", "rc_new")
    assert saved.plan.title == "Battery recycling plan"
    assert saved.plan.steps == ["Survey: Read the sources", "Compare: Weigh the findings"]
    assert saved.plan.eta_text == "about 8 minutes"
    assert saved.plan.confirm_prompt == "Start research"
    assert saved.plan.response_text == "Here is my plan."


def test_a_chat_without_a_plan_yields_none():
    assert find_saved_plan([]) is None
    assert find_saved_plan([turn("r_1", [candidate("rc_1", "Just an answer.")])]) is None


def test_the_turns_are_read_from_the_conversation_response():
    turns = [turn("r_1", [candidate("rc_1", "Here is my plan.", plan_payload())])]
    text = wire([["wrb.fr", "hNvQHb", json.dumps([turns]), None, None, None, "generic"]])
    assert turns_from_response(text) == turns


def test_an_unreadable_response_has_no_turns():
    assert turns_from_response(")]}'\n\n{not json") == []


def test_read_saved_plan_asks_for_the_chat_turns_and_returns_the_plan():
    turns = [turn("r_1", [candidate("rc_1", "Here is my plan.", plan_payload())])]
    client = FakeBatchClient(wire([["wrb.fr", "hNvQHb", json.dumps([turns]), None, None, None, "generic"]]))

    saved = asyncio.run(read_saved_plan(client, "c_saved"))

    assert client.payloads == ['["c_saved",10,null,1,[1],[4],null,1]']
    assert client.close_on_error == [False]  # a failed read must not close the shared client
    assert saved is not None
    assert saved.plan.title == "Battery recycling plan"
    assert (saved.rid, saved.rcid) == ("r_1", "rc_1")


def test_read_saved_plan_is_none_while_the_chat_has_no_plan():
    turns = [turn("r_1", [candidate("rc_1", "Still thinking.")])]
    client = FakeBatchClient(wire([["wrb.fr", "hNvQHb", json.dumps([turns]), None, None, None, "generic"]]))
    assert asyncio.run(read_saved_plan(client, "c_saved")) is None


def test_the_log_line_says_how_far_the_saved_turn_has_got():
    assert describe_turns([]) == "no turns"
    turns = [turn("r_1", [candidate("rc_1", "Still thinking.")])]
    assert describe_turns(turns) == "1 turns, newest candidate status 2, rich fields set []"
    with_plan = [turn("r_2", [candidate("rc_2", "Here is my plan.", plan_payload())])]
    assert "rich fields set [55]" in describe_turns(with_plan)
