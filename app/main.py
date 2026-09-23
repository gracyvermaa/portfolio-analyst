"""
FastAPI app.

/chat            — the WhatsApp-style conversation endpoint
/admin/*         — read-only business interface endpoints
/users           — lets the chat UI simulate "picking a contact" (no real auth)
/                — chat UI
/admin           — business interface UI
"""
from typing import Optional
from pathlib import Path
import time
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Conversation, Message, ToolCall
from app.agent import build_agent

app = FastAPI(title="AI Real Estate Portfolio Analyst")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def chat_ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin_ui():
    return FileResponse(STATIC_DIR / "admin.html")


# --- chat ---------------------------------------------------------------

class ChatRequest(BaseModel):
    user_id: str
    conversation_id: Optional[int] = None
    message: str


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str


def _load_history(db: Session, conversation_id: int) -> list:
    """Reconstructs prior turns as the {role, content} dicts create_agent expects."""
    msgs = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).all()
    return [{"role": m.role, "content": m.content} for m in msgs]


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.user_id == req.user_id).first()
    if not user:
        raise HTTPException(404, f"unknown user_id {req.user_id}")

    if req.conversation_id:
        convo = db.query(Conversation).filter(
            Conversation.id == req.conversation_id, Conversation.user_id == req.user_id
        ).first()
        if not convo:
            raise HTTPException(404, "conversation not found for this user")
    else:
        convo = Conversation(user_id=req.user_id, status="open")
        db.add(convo)
        db.commit()
        db.refresh(convo)

    history = _load_history(db, convo.id)

    # persist the incoming user message before calling the agent, so it's
    # never lost even if the model call fails
    user_msg = Message(conversation_id=convo.id, role="user", content=req.message)
    db.add(user_msg)
    db.commit()

    try:
        start = time.perf_counter()
        agent, callback = build_agent(db, owner=req.user_id, conversation_id=convo.id)
        result = agent.invoke(
            {"messages": history + [{"role": "user", "content": req.message}]},
            config={"callbacks": [callback]} if callback else {},
        )
        reply_text = result["messages"][-1].content
        turn_latency_ms = int((time.perf_counter() - start) * 1000)
    except Exception as e:
        # log the failure as a flagged conversation for the business team,
        # rather than losing the turn silently
        convo.flagged = True
        convo.flag_reason = f"agent error: {e}"
        db.commit()
        raise HTTPException(500, f"agent failed to respond: {e}")

    assistant_msg = Message(conversation_id=convo.id, role="assistant", content=reply_text, latency_ms=turn_latency_ms)
    db.add(assistant_msg)
    db.commit()

    return ChatResponse(conversation_id=convo.id, reply=reply_text)


@app.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [{"user_id": u.user_id, "name": u.name, "city": u.city} for u in users]


@app.get("/conversations/current")
def current_conversation(user_id: str, db: Session = Depends(get_db)):
    """Returns the user's most recent conversation with full message history,
    so the chat UI can restore it instead of starting a blank thread every time
    a contact is selected or the page is reloaded."""
    convo = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.started_at.desc())
        .first()
    )
    if not convo:
        return {"conversation_id": None, "messages": []}

    return {
        "conversation_id": convo.id,
        "messages": [{"role": m.role, "content": m.content} for m in convo.messages],
    }


# --- admin / business interface ------------------------------------------

@app.get("/admin/conversations")
def list_conversations(db: Session = Depends(get_db)):
    convos = db.query(Conversation).order_by(Conversation.started_at.desc()).all()
    out = []
    for c in convos:
        last_msg = c.messages[-1] if c.messages else None
        out.append({
            "conversation_id": c.id,
            "user_id": c.user_id,
            "user_name": c.user.name,
            "message_count": len(c.messages),
            "started_at": c.started_at.isoformat(),
            "last_activity": last_msg.created_at.isoformat() if last_msg else None,
            "flagged": c.flagged,
            "flag_reason": c.flag_reason,
        })
    return out


@app.get("/admin/conversations/{conversation_id}")
def conversation_detail(conversation_id: int, db: Session = Depends(get_db)):
    convo = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not convo:
        raise HTTPException(404, "conversation not found")

    timeline = []
    for m in convo.messages:
        timeline.append({"type": "message", "role": m.role, "content": m.content, "latency_ms": m.latency_ms, "at": m.created_at.isoformat()})
    for tc in convo.tool_calls:
        timeline.append({
            "type": "tool_call", "tool_name": tc.tool_name, "input": tc.input_json,
            "output": tc.output_json, "status": tc.status, "latency_ms": tc.latency_ms,
            "at": tc.created_at.isoformat(),
        })
    timeline.sort(key=lambda x: x["at"])

    return {
        "conversation_id": convo.id,
        "user_id": convo.user_id,
        "user_name": convo.user.name,
        "flagged": convo.flagged,
        "flag_reason": convo.flag_reason,
        "timeline": timeline,
    }
