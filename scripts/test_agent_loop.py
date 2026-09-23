"""
Verifies the agent loop mechanics (tool-calling, DB logging) without hitting
a real model API. A fake chat model returns a scripted tool_call on its
first turn (portfolio_summary for U002), then a scripted final answer on
its second turn. Confirms: the tool actually runs against SQLite, the
result flows back to the model, and ToolLoggingCallback writes a row.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.database import SessionLocal
from app.models import Conversation, ToolCall
from app.agent import build_agent


class ScriptedFakeModel(BaseChatModel):
    """Returns a tool call once, then a final text answer."""
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):
        # Real models use this to advertise tool schemas; the fake model
        # ignores schemas and just plays back its script, so return as-is.
        return self

    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "portfolio_summary", "args": {}, "id": "call_1"}],
            )
        else:
            msg = AIMessage(content="Your total portfolio value is Rs 19.3 Cr across 3 properties, all residential.")
        return ChatResult(generations=[ChatGeneration(message=msg)])


db = SessionLocal()
convo = Conversation(user_id="U002", status="open")
db.add(convo)
db.commit()
db.refresh(convo)

agent, callback = build_agent(db, owner="U002", conversation_id=convo.id, model=ScriptedFakeModel())

result = agent.invoke(
    {"messages": [{"role": "user", "content": "what does my portfolio look like"}]},
    config={"callbacks": [callback]} if callback else {},
)

final_message = result["messages"][-1]
print("FINAL AGENT REPLY:", final_message.content)

logged = db.query(ToolCall).filter(ToolCall.conversation_id == convo.id).all()
print(f"\nTOOL CALLS LOGGED: {len(logged)}")
for tc in logged:
    print(f"  - {tc.tool_name} | status={tc.status} | latency_ms={tc.latency_ms} | output={tc.output_json[:120]}")

db.close()
