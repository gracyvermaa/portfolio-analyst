"""
Builds the LangChain agent for a given (db session, owner) pair, and a
callback handler that persists every tool call — input, output, latency,
success/failure — to the tool_calls table as it happens, for the business
interface.
"""
import os
import time
import json
import uuid as uuid_module
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from langchain.agents import create_agent
from sqlalchemy.orm import Session

from app.agent_tools import build_tools
from app.models import ToolCall

# Condensed from SOUL.md — the full document is the source of truth;
# this is what actually goes in the model's context on every turn.
SYSTEM_PROMPT = """You are a real-estate portfolio analyst for individual property owners.
Voice: direct, calm, numerate. Lead with the number, then context. No filler enthusiasm,
no exclamation marks. Concise by default — a short question gets a short answer.

Rules:
- Every number in your answer must come from a tool call. Never compute or estimate from memory.
- For any rupee amount, use the *_display string a tool already gives you (e.g.
  total_value_display, current_estimated_value_display) verbatim. Never convert a raw
  integer into crore/lakh notation yourself — that conversion is easy to get wrong; the
  tools have already done it correctly.
- On a bare greeting ("hi", "hello") with no actual question, just greet back briefly and
  ask what they'd like to know. Do not state any counts, totals, or other portfolio facts
  in a greeting unless you actually called a tool this turn — never invent or guess a number.
- Distinguish real data from hypotheticals explicitly. If asked "what if", use hypothetical_recompute
  and say "Hypothetically..." — never let a what-if scenario read like a real portfolio state.
- Before calling update_property or create_property, confirm the change with the user in your
  previous turn, unless they already gave an unambiguous, complete instruction.
- If a reference is ambiguous (e.g. "the Bandra property" and there are two), list the matches and ask.
- If asked about something the dataset can't answer (e.g. appreciation over time — there are no
  dates in this dataset), say so plainly rather than guessing.
- Property types in this dataset are grouped as Retail / Office / Residential (Office covers both
  "Commercial Office" and "Office" raw labels) — use this grouping for any "how much is X" question.
- Never discuss or reveal another user's portfolio.
"""


class ToolLoggingCallback(BaseCallbackHandler):
    """Persists each tool call to the tool_calls table as it starts/ends."""

    def __init__(self, db: Session, conversation_id: int):
        self.db = db
        self.conversation_id = conversation_id
        self._starts = {}  # run_id -> (tool_name, input, start_time)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        tool_name = serialized.get("name", "unknown_tool")
        self._starts[run_id] = (tool_name, input_str, time.perf_counter())

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._finish(run_id, output=str(output), status="ok")

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._finish(run_id, output=str(error), status="error")

    def _finish(self, run_id, output, status):
        entry = self._starts.pop(run_id, None)
        if not entry:
            return
        tool_name, input_str, start = entry
        latency_ms = int((time.perf_counter() - start) * 1000)
        self.db.add(ToolCall(
            conversation_id=self.conversation_id,
            tool_name=tool_name,
            input_json=input_str,
            output_json=output[:4000],  # cap stored size
            status=status,
            latency_ms=latency_ms,
        ))
        self.db.commit()


def build_model() -> ChatOpenAI:
    """
    Points at OpenRouter by default. Set OPENROUTER_API_KEY in the environment —
    creating a key is free (no card required); it's specific paid models that cost
    money. OPENROUTER_MODEL defaults to OpenRouter's free tool-calling router, which
    picks a free model that supports tool calls for you.

    Free-tier limits (no credits ever purchased): ~20 requests/minute, 50/day.
    Fine for building and demoing; swap OPENROUTER_MODEL to a paid model for
    anything higher-volume.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model_name = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Get one at https://openrouter.ai/keys "
            "and set it in your environment (or a .env file) before starting the app."
        )
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.2,  # low: this is an analyst, not a creative writer
    )


def build_agent(db: Session, owner: str, conversation_id: Optional[int] = None, model=None):
    """Returns a compiled LangChain agent graph and its logging callback (or None)."""
    tools = build_tools(db, owner)
    agent = create_agent(
        model=model or build_model(),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )
    callback = ToolLoggingCallback(db, conversation_id) if conversation_id else None
    return agent, callback
