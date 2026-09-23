"""
ORM models.

Two tables (`users`, `properties`) mirror the seed CSVs. Four more
(`conversations`, `messages`, `tool_calls`, `session_context`) are created
fresh by the app and drive both the chat agent's memory and the business
interface — every tool call the agent makes is written here as it happens,
not reconstructed after the fact.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, ForeignKey, Text, DateTime
)
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# --- Seed data -------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    city = Column(String)
    preferences = Column(String)               # e.g. "Commercial / Retail"
    preferred_locations = Column(String)        # free text, "/" separated
    portfolio_value_preference_inr = Column(String)  # single value or "min-max" range, kept as text

    properties = relationship("Property", back_populates="owner")
    conversations = relationship("Conversation", back_populates="user")


class Property(Base):
    __tablename__ = "properties"

    property_id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False)

    property_type = Column(String, nullable=False)      # raw label, e.g. "Commercial Office"
    normalized_type = Column(String, nullable=False)     # collapsed: Retail / Office / Residential
    sub_type = Column(String)
    location = Column(String, nullable=False)            # "Locality, City"

    area_sqft = Column(Integer)
    current_estimated_value_inr = Column(Integer)
    purchase_price_inr = Column(Integer, nullable=True)   # NULL, not 0, when unknown
    annual_rent_inr = Column(Integer, default=0)

    occupancy_status = Column(String)   # Tenanted / Vacant / Self-occupied
    tenant_status = Column(String)      # Yes / No
    ownership_percent = Column(Integer, default=100)
    status = Column(String, default="Active")

    owner = relationship("User", back_populates="properties")


# --- Conversation state ------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    started_at = Column(DateTime, default=utcnow)
    status = Column(String, default="open")       # open / closed
    flagged = Column(Boolean, default=False)
    flag_reason = Column(String, nullable=True)

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")
    tool_calls = relationship("ToolCall", back_populates="conversation", order_by="ToolCall.created_at")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)   # "user" | "assistant"
    content = Column(Text, nullable=False)
    latency_ms = Column(Integer, nullable=True)  # full turn latency, set on assistant messages only
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=True)  # the assistant msg it supported

    tool_name = Column(String, nullable=False)
    input_json = Column(Text)
    output_json = Column(Text)
    status = Column(String, default="ok")   # ok | error
    latency_ms = Column(Integer)
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="tool_calls")


class SessionContext(Base):
    """
    Small per-conversation key/value store, e.g.:
      key="last_property_ids", value_json='["P001","P003"]'
      key="active_hypothetical", value_json='{"exclude": ["P001"]}'
    Lets follow-ups like "how would that change it" resolve without
    re-parsing the entire message history every turn.
    """
    __tablename__ = "session_context"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    key = Column(String, nullable=False)
    value_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# --- Type normalization ------------------------------------------------------

# Collapses the dataset's inconsistent labels into three display categories.
# Raw property_type/sub_type are preserved unchanged on the row; this mapping
# only affects grouping/aggregation questions ("how much is retail" etc).
TYPE_NORMALIZATION = {
    "retail": "Retail",
    "commercial office": "Office",
    "office": "Office",
    "residential": "Residential",
}


def normalize_type(raw_property_type: str) -> str:
    return TYPE_NORMALIZATION.get(raw_property_type.strip().lower(), raw_property_type)
