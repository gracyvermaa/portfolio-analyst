"""
Wraps app/tools.py functions as LangChain StructuredTools.

Each tool is built per-request, closed over a specific `db` session and
`owner` (user_id) — the LLM never sees or controls which user it's acting
as, so it structurally cannot cross into another user's data.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from app import tools as t


def build_tools(db: Session, owner: str) -> List[StructuredTool]:

    # --- search_properties ---
    class SearchArgs(BaseModel):
        property_type: Optional[str] = Field(None, description="Retail, Office, or Residential")
        location_contains: Optional[str] = Field(None, description="substring match, e.g. 'Mumbai' or 'Bandra'")
        min_value: Optional[int] = Field(None, description="minimum current_estimated_value_inr")
        max_value: Optional[int] = Field(None, description="maximum current_estimated_value_inr")
        occupancy_status: Optional[str] = Field(None, description="Tenanted, Vacant, or Self-occupied")

    def _search(**kwargs):
        return t.search_properties(db, owner, **kwargs)

    search_tool = StructuredTool.from_function(
        func=_search, name="search_properties", args_schema=SearchArgs,
        description="Find properties in the user's portfolio matching filters. Use for any 'show me' / 'which properties' style question.",
    )

    # --- portfolio_summary ---
    class SummaryArgs(BaseModel):
        pass

    def _summary(**kwargs):
        return t.portfolio_summary(db, owner)

    summary_tool = StructuredTool.from_function(
        func=_summary, name="portfolio_summary", args_schema=SummaryArgs,
        description="Total portfolio value, rent, yield, and breakdown by property type. Use for 'what does my portfolio look like' / 'total value' style questions.",
    )

    # --- portfolio_metric ---
    class MetricArgs(BaseModel):
        metric: str = Field(..., description="one of: sum, avg, max, min, count")
        field: str = Field("current_estimated_value_inr", description="current_estimated_value_inr, annual_rent_inr, or area_sqft")
        group_by: Optional[str] = Field(None, description="normalized_type or location, to break the aggregate down by group")
        property_type: Optional[str] = Field(None, description="filter to Retail, Office, or Residential first")
        location_contains: Optional[str] = Field(None, description="filter to locations containing this text first")

    def _metric(**kwargs):
        return t.portfolio_metric(db, owner, **kwargs)

    metric_tool = StructuredTool.from_function(
        func=_metric, name="portfolio_metric", args_schema=MetricArgs,
        description="A single computed number (or grouped breakdown) over the portfolio — highest rent, average value, count of properties in a location, etc.",
    )

    # --- compare_segments ---
    class CompareArgs(BaseModel):
        segment_a: str = Field(..., description="Retail, Office, or Residential")
        segment_b: str = Field(..., description="Retail, Office, or Residential")

    def _compare(**kwargs):
        return t.compare_segments(db, owner, **kwargs)

    compare_tool = StructuredTool.from_function(
        func=_compare, name="compare_segments", args_schema=CompareArgs,
        description="Side-by-side comparison of two property-type segments (value, rent, yield, count).",
    )

    # --- hypothetical_recompute ---
    class HypotheticalArgs(BaseModel):
        exclude_property_ids: Optional[List[str]] = Field(None, description="property_ids to hypothetically remove")
        value_overrides: Optional[Dict[str, int]] = Field(None, description="property_id -> hypothetical new value_inr, without saving it")

    def _hypothetical(**kwargs):
        return t.hypothetical_recompute(db, owner, **kwargs)

    hypothetical_tool = StructuredTool.from_function(
        func=_hypothetical, name="hypothetical_recompute", args_schema=HypotheticalArgs,
        description="Recompute portfolio totals under a what-if scenario (excluding properties or overriding a value). Does NOT change real data — use this for any 'what if' question, never search/update tools.",
    )

    # --- create_property ---
    class CreateArgs(BaseModel):
        property_type: Optional[str] = None
        sub_type: Optional[str] = None
        location: Optional[str] = None
        area_sqft: Optional[int] = None
        current_estimated_value_inr: Optional[int] = None
        annual_rent_inr: Optional[int] = None
        occupancy_status: Optional[str] = None
        tenant_status: Optional[str] = None

    def _create(**kwargs):
        fields = {k: v for k, v in kwargs.items() if v is not None}
        return t.create_property(db, owner, fields)

    create_tool = StructuredTool.from_function(
        func=_create, name="create_property", args_schema=CreateArgs,
        description="Add a new property. If required fields (property_type, location, area_sqft, current_estimated_value_inr) are missing, this returns which ones — ask the user for those before calling again.",
    )

    # --- update_property ---
    class UpdateArgs(BaseModel):
        property_id: str = Field(..., description="e.g. P001 — resolve from context if the user didn't give an ID directly")
        field: str = Field(..., description="which field to change, e.g. current_estimated_value_inr")
        new_value: Any = Field(..., description="the new value")

    def _update(**kwargs):
        return t.update_property(db, owner, **kwargs)

    update_tool = StructuredTool.from_function(
        func=_update, name="update_property", args_schema=UpdateArgs,
        description="Update one field on an existing property the user owns. Only call this after the user has confirmed the change.",
    )

    return [
        search_tool, summary_tool, metric_tool, compare_tool,
        hypothetical_tool, create_tool, update_tool,
    ]
