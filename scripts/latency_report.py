"""
Run this after you've had a few real conversations through the chat UI.
Reads the tool_calls table (populated live by ToolLoggingCallback) and
prints the numbers the assignment's Performance section (§5) asks for:
typical response time, where time is spent, and per-tool latency.

Usage: python -m scripts.latency_report
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
from app.database import SessionLocal
from app.models import ToolCall, Message, Conversation


def main():
    db = SessionLocal()

    tool_calls = db.query(ToolCall).all()
    assistant_msgs = db.query(Message).filter(
        Message.role == "assistant", Message.latency_ms.isnot(None)
    ).all()

    if not tool_calls and not assistant_msgs:
        print("No traffic logged yet. Have a few real conversations through the chat UI first,")
        print("then re-run this script.")
        db.close()
        return

    if assistant_msgs:
        turn_latencies = [m.latency_ms for m in assistant_msgs]
        print("=" * 60)
        print("FULL TURN LATENCY (ms) — end-to-end, request in to reply out")
        print("=" * 60)
        n = len(turn_latencies)
        avg = sum(turn_latencies) / n
        sorted_lat = sorted(turn_latencies)
        p50 = sorted_lat[n // 2]
        p90 = sorted_lat[int(n * 0.9)] if n > 1 else sorted_lat[0]
        print(f"  n={n}  avg={avg:.0f}ms  p50={p50}ms  p90={p90}ms  min={min(turn_latencies)}ms  max={max(turn_latencies)}ms")
        print()

    if not tool_calls:
        db.close()
        return

    # --- per-tool latency ---
    by_tool = defaultdict(list)
    for tc in tool_calls:
        by_tool[tc.tool_name].append(tc.latency_ms or 0)

    print("=" * 60)
    print("PER-TOOL LATENCY (ms)")
    print("=" * 60)
    for name, latencies in sorted(by_tool.items()):
        n = len(latencies)
        avg = sum(latencies) / n
        print(f"  {name:<24} n={n:<4} avg={avg:>7.1f}ms  min={min(latencies):>6}ms  max={max(latencies):>6}ms")

    total_tool_time = sum(tc.latency_ms or 0 for tc in tool_calls)
    print(f"\n  Total tool_calls logged: {len(tool_calls)}")
    print(f"  Total tool execution time across all calls: {total_tool_time}ms")

    # --- error rate ---
    errors = [tc for tc in tool_calls if tc.status == "error"]
    print(f"  Tool call error rate: {len(errors)}/{len(tool_calls)} ({100*len(errors)/len(tool_calls):.1f}%)")

    # --- tool calls per conversation turn (proxy for how many model round-trips a turn costs) ---
    by_conversation = defaultdict(int)
    for tc in tool_calls:
        by_conversation[tc.conversation_id] += 1

    print("\n" + "=" * 60)
    print("TOOL CALLS PER CONVERSATION")
    print("=" * 60)
    for conv_id, count in sorted(by_conversation.items()):
        n_messages = db.query(Message).filter(Message.conversation_id == conv_id).count()
        n_user_turns = db.query(Message).filter(
            Message.conversation_id == conv_id, Message.role == "user"
        ).count()
        avg_tools_per_turn = count / n_user_turns if n_user_turns else 0
        print(f"  conversation {conv_id}: {count} tool calls across {n_user_turns} user turns "
              f"({avg_tools_per_turn:.1f} tool calls/turn)")

    print("\n" + "=" * 60)
    print("NOTES FOR THE WRITEUP")
    print("=" * 60)
    print("""
  - Full turn latency (top of this report) is the number that matters to a user —
    it's everything: model round-trip(s) + tool execution + our own overhead.
  - Tool execution time (per-tool table above) is almost certainly a small slice
    of that. Subtracting rough avg tool time per turn from avg full-turn latency
    gives a good estimate of how much is model round-trip time — expect it to
    dominate, since SQLite queries on this dataset size run in single-digit ms.
  - A turn with N tool calls costs roughly N+1 model round-trips under a standard
    tool-calling loop (one to decide each tool call, one to compose the final
    answer) — the "tool calls per conversation" table above is a direct proxy for
    how many round-trips each turn actually took.
  - At higher volume, this points at the real levers: cache portfolio_summary per
    user (invalidate on write), reduce round-trips for simple single-tool turns,
    and consider a faster model for routing vs. a stronger one only for genuinely
    multi-step turns (comparisons, hypotheticals).
""")

    db.close()


if __name__ == "__main__":
    main()
