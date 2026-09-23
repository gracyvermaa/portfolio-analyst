# AI Real Estate Portfolio Analyst

A conversational AI agent over a real-estate portfolio dataset, plus a read-only
business interface. Built for the AI Real Estate Portfolio Analyst engineering assignment.

See `ARCHITECTURE.md` for design rationale and `SOUL.md` for the agent's defined
behaviour, tone, and boundaries.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and put your OpenRouter key in OPENROUTER_API_KEY
# (get one at https://openrouter.ai/keys)

python -m scripts.load_data     # seeds portfolio.db from data/*.csv — safe to re-run
uvicorn app.main:app --reload
```

Then open:
- **http://localhost:8000/** — the chat UI (pick a user on the left, simulates WhatsApp)
- **http://localhost:8000/admin** — the business interface

## Project layout

```
app/
  database.py      SQLAlchemy engine/session
  models.py         users, properties, conversations, messages, tool_calls, session_context
  tools.py           the 7 core functions (search, analyse, hypothetical, create, update)
  agent_tools.py     LangChain tool wrappers, scoped per-request to (db, owner)
  agent.py           system prompt, OpenRouter model, tool-call logging callback
  main.py            FastAPI app — /chat, /users, /admin/*, and the two UI routes
scripts/
  load_data.py        CSV -> SQLite loader
  test_agent_loop.py  fake-model test proving the tool-calling loop + logging work
static/
  index.html           WhatsApp-style chat UI
  admin.html           business interface UI
data/                 the seed CSVs
SOUL.md                agent behaviour definition
ARCHITECTURE.md         design rationale
```

## Testing without spending API credits

`python -m scripts.test_agent_loop` runs the tool-calling loop against a scripted fake
model (no network call) — useful for confirming the wiring still works after a change,
without hitting OpenRouter.

## Known limitations

- **Free-tier model latency (~15-20s per turn).** `openrouter/free` routes to whichever
  free, tool-calling-capable model has capacity — shared, rate-limited infrastructure with
  no latency guarantee. Tool execution itself is single-digit milliseconds (verified via
  `scripts/latency_report.py`); essentially all of that time is the model round-trip, not
  our code. Deliberate trade-off for this assignment: zero cost to run and grade, at the
  cost of responsiveness. At production volume, or if snappier UX mattered more than
  spend, swapping `OPENROUTER_MODEL` to a fast paid model (e.g. a Flash/mini-tier model)
  is a one-line `.env` change with no code change — response time would drop to roughly
  1-3s based on typical throughput for that tier.
- Using OpenRouter's free tier (`openrouter/free`, ~20 requests/min and 50/day on a
  key that's never bought credits) — fine for building and demoing, not for real
  volume.
- SQLite is single-file/single-tenant — fine for 4 users / 12 properties, not a production
  data store at scale.
- No auth (matches the assignment's "no authenticated data source required").
- Multi-turn reference resolution ("that property", "how would that change it") currently
  relies on the model reading recent conversation history rather than an explicit session-
  context store (the `session_context` table exists in the schema for this but isn't
  populated yet) — works well in practice for 2-3 turn follow-ups, would need that table
  wired in for longer, more ambiguous threads.
- No dates anywhere in the dataset, so the agent is instructed to decline time-based
  questions (appreciation, growth) honestly rather than fabricate a trend.
