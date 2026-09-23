# Decision Log

The most significant engineering decisions made while building this system, and the
trade-offs behind each. Ordered roughly by how much they shaped the rest of the system.

---

### 1. Single agent with 7 tools, not multi-agent

**Decision:** One LangChain agent (`create_agent`) with a flat set of 7 tools, rather than a
router agent delegating to specialist sub-agents (search agent, analysis agent, CRUD agent).

**Why:** The tool surface is small enough that a well-written system prompt and clear tool
docstrings let the model route correctly on its own. A multi-agent split adds at least one
extra model round-trip per turn (the router's own reasoning step) for no accuracy benefit at
this scale — 7 tools is well within what a single tool-calling loop handles reliably.

**Trade-off accepted:** This wouldn't scale as cleanly if the tool count grew into the dozens,
or if different tool categories needed genuinely different system prompts/personas. Neither
is true here.

---

### 2. SQLite over a vector store or an in-memory dict

**Decision:** All persistence — property data, conversation history, tool-call logs — lives in
one SQLite file, queried with SQLAlchemy.

**Why:** The dataset is small, structured, and relational. Questions like "total portfolio
value" or "highest rent" are exact aggregations, not semantic retrieval — SQL's `SUM`/`MAX`/
`GROUP BY` is both more accurate and faster than embedding a property list and hoping
similarity search surfaces the right rows. A vector store would add infrastructure and a
failure mode (retrieval missing the relevant row) that plain SQL doesn't have.

**Trade-off accepted:** SQLite is single-file and not built for concurrent writers at scale.
Fine for 4 users; would need Postgres for anything real.

---

### 3. Tools return structured JSON, the LLM writes the sentence

**Decision:** Every tool function returns a plain dict (counts, totals, breakdowns) — never a
pre-written sentence. The agent composes the final natural-language answer from that data.

**Why:** Keeps the "thinking" (what to compute) and the "speaking" (how to phrase it)
separable and independently testable. It's also what makes the fake-model test
(`scripts/test_agent_loop.py`) possible — the tool layer can be verified deterministically
without needing a real model call, since its output is just data.

---

### 4. `normalized_type` computed at load time, not at query time

**Decision:** The dataset's inconsistent labels (`Commercial Office` vs `Office`) are collapsed
into `{Retail, Office, Residential}` once, when data loads, and stored as a column — not
re-derived on every query.

**Why:** Grouping logic needs to be consistent across every tool that touches it
(`portfolio_summary`, `portfolio_metric`, `compare_segments`). Computing it once and storing it
guarantees that; re-deriving it inline in each tool risked the mapping drifting between tools
over time as the codebase grew.

**Trade-off accepted:** Changing the normalization rule later means re-running the loader, not
just editing one function. Acceptable given the mapping is small and stable.

---

### 5. `purchase_price_inr` stored as `NULL`, never defaulted to 0

**Decision:** Blank purchase prices load as `NULL` in SQLite, not `0`.

**Why:** The dataset intentionally leaves this blank for most rows. Defaulting to `0` would
make any future "gain since purchase" calculation silently show a fictional 100%+ return
instead of correctly showing "unknown." The agent is instructed to say plainly that
appreciation/growth questions aren't answerable from this dataset (no dates exist either)
rather than compute something from a defaulted value.

---

### 6. Hypotheticals never touch the database

**Decision:** `hypothetical_recompute` takes exclusions/overrides as tool arguments and
recomputes in memory — it never writes anything, even temporarily.

**Why:** The assignment explicitly calls out distinguishing real data from hypothetical
analysis as a requirement (§2.3). The safest way to guarantee that distinction never leaks is
structural: the hypothetical path has no code path to a `db.commit()` at all, rather than
relying on the LLM to "remember" not to save it.

---

### 7. Confirm-before-write is a prompt instruction, not a hard tool gate

**Decision:** `update_property` and `create_property` will execute immediately if called — the
"confirm with the user first" rule lives in the system prompt (SOUL.md / agent.py), not as a
mandatory human-in-the-loop step enforced by the code.

**Why:** A hard gate (e.g. a pending-confirmation table, a second `/chat` round-trip required)
would meaningfully complicate the request flow for a 1-2 day scope, and the assignment's
sample flow ("Update the Bandra property's value to ₹14 Cr") reads as a single-turn action
when the user's intent is already unambiguous.

**Trade-off accepted — the honest risk here:** this depends on the model reliably following
the prompted instruction. It's the one place in the system where a real write depends on LLM
judgment rather than code enforcing it. If testing shows the model skips confirmation when it
shouldn't, the next step would be a proper pending-action pattern (tool returns "would write
X, confirm?" and a second call actually commits).

---

### 8. OpenRouter's free router (`openrouter/free`) as the default model

**Decision:** Default `OPENROUTER_MODEL` is `openrouter/free`, which auto-selects a free,
tool-calling-capable model, rather than a paid model.

**Why:** Keeps the project runnable by anyone with a no-cost OpenRouter key, matching the
assignment's "no private/paid data source required" spirit for the demo itself. `OPENROUTER_MODEL`
is a single env var, so swapping to a paid model for better reasoning or higher throughput is a
one-line change, not a code change.

**Trade-off accepted:** The free tier's rate limit (~20 req/min, 50/day on a key that's never
purchased credits) is fine for development and a demo walkthrough, not for real usage volume.
Documented in the latency/scale discussion.

---

### 9. Business interface reads the same tables the agent writes, no separate logging system

**Decision:** `/admin/*` endpoints query `conversations`, `messages`, and `tool_calls`
directly — there's no separate observability pipeline.

**Why:** Every tool call is already persisted in real time via `ToolLoggingCallback` (see
`app/agent.py`) as part of normal operation, not as an afterthought. Building a second logging
path risked the two falling out of sync; reading the same source of truth the agent writes to
guarantees the business team sees exactly what happened, including partial/failed turns.

---

### 10. Conversation memory via raw history replay, not a session-context store

**Decision:** Multi-turn context ("that property", "how would that change it") currently works
by passing the full prior message history back to the model each turn. The `session_context`
table exists in the schema but isn't populated yet.

**Why:** For the assignment's dataset size and expected conversation length (a handful of
turns), replaying history is simpler and the model's own attention over 5-10 prior turns is
generally reliable for pronoun/reference resolution — no extra engineering needed to get
correct behavior for the sample flows in §2.3.

**Known limitation:** This gets more expensive (larger prompt every turn) and less reliable
the longer a conversation runs. `session_context` was designed in from the start specifically
so a later pass could pin down "last discussed property_ids" and "active hypothetical" state
explicitly, without a schema change — see README's Known Limitations.

---

### 11. Money is formatted by the tools, never by the model

**Decision:** Every tool that returns a rupee amount (`portfolio_summary`, `portfolio_metric`,
`hypothetical_recompute`, `compare_segments`, `search_properties`) also returns a pre-formatted
`*_display` string in Indian crore/lakh notation (e.g. `"₹12,90,00,000 (₹12.90 Cr)"`). The system
prompt instructs the model to relay that string verbatim rather than converting the raw
integer itself.

**Why:** Found via real testing — a free-tier model converted a correct `129000000` from a
tool into "₹1,29,00,000 (₹1.29 Cr)", a 10x error, because it tried to do the crore/lakh
digit-grouping arithmetic itself and got it wrong. Indian numbering (groups of 2 after the
first 3 digits, not groups of 3) is exactly the kind of arithmetic small/free models are
unreliable at. Moving the formatting into deterministic Python code removes an entire class
of correctness bugs regardless of which model is running — this isn't specific to the free
model we're using, any model can make this mistake.

---

### 12. Accepted free-tier latency (~15-20s/turn) as a documented trade-off, not solved

**Decision:** Left `OPENROUTER_MODEL=openrouter/free` as the default rather than switching to
a paid model, even though it means ~15-20 second response times.

**Why:** This is a hiring-assignment demo, not a production deployment — there's no reason to
spend money running it. Confirmed via `scripts/latency_report.py` that essentially all of that
time is model round-trip, not tool execution (which is single-digit ms) — so the fix, if ever
needed, is purely a one-line `.env` model swap, not a code or architecture change. Documented
here rather than "fixed" so it doesn't look like an oversight: it's a conscious cost-vs-latency
choice, and the system is built so reversing it costs nothing.

---

### 13. Fixed: chat UI could render a reply into the wrong open conversation

**Found via real testing:** given the free-tier model's 15-20s response time, switching to a
different contact in the chat UI while a reply was still in flight caused that reply to render
into whichever conversation was on screen when it arrived — not the one it was actually for.

**Root cause:** `sendMessage()` in `static/index.html` used the global `currentUser`/
`conversationId` variables to decide where to render the response *after* the `await`,
rather than the values captured at the moment the request was sent. Selecting a new contact
also destroys and rebuilds the `#messages` DOM element, so a stale reference plus a stale
global variable combined to write the old request's answer into the new contact's window —
and would also have overwritten `conversationId` with the wrong value, risking subsequent
messages for the new contact being posted against someone else's conversation.

**Fix:** capture `requestedUserId`/`requestedConversationId` synchronously before the `fetch`
call; after the response returns, only touch the DOM or the global `conversationId` if
`currentUser.user_id` still matches what was requested. If the user has switched away, the
reply is left alone — it's already correctly persisted server-side under the right
conversation via the `user_id`/`conversation_id` sent in the original request, and will show
up correctly next time that conversation is reopened.

**Important distinction:** this was a rendering bug only. The backend received and stored the
correct `user_id` on every request regardless (captured before the same `await`, same as the
render logic now is) — no property or conversation data was ever attributed to the wrong user
in the database. The confusion was entirely in what the browser drew on screen.
