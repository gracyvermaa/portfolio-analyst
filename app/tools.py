"""
Tool layer.

Every function here takes `db` and `owner` (a user_id) as the first two
arguments and never queries across owners — the agent can only ever see
and modify the requesting user's own properties. Functions return plain
dicts/lists (JSON-serializable), not prose; the agent composes the final
answer from these.
"""
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Property, normalize_type

VALID_METRICS = {"sum", "avg", "max", "min", "count"}
VALID_FIELDS = {"current_estimated_value_inr", "annual_rent_inr", "area_sqft"}


def _indian_grouping(n: int) -> str:
    """1,29,00,000 style digit grouping (last 3 digits, then pairs)."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        return s
    last3, rest = s[-3:], s[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    sign = "-" if n < 0 else ""
    return sign + ",".join(parts) + "," + last3


def format_inr(amount) -> Optional[str]:
    """
    Renders an INR amount as '₹12,90,00,000 (₹12.9 Cr)' etc. Tools return this
    pre-computed specifically so the model relays it rather than converting raw
    integers into crore/lakh notation itself — that conversion is exactly the
    kind of digit-grouping arithmetic small/free models get wrong.
    """
    if amount is None:
        return None
    amount = int(amount)
    grouped = _indian_grouping(amount)
    if abs(amount) >= 1_00_00_000:
        return f"\u20b9{grouped} (\u20b9{amount / 1_00_00_000:.2f} Cr)"
    if abs(amount) >= 1_00_000:
        return f"\u20b9{grouped} (\u20b9{amount / 1_00_000:.2f} L)"
    return f"\u20b9{grouped}"


def _base_query(db: Session, owner: str):
    return db.query(Property).filter(Property.user_id == owner, Property.status == "Active")


def _serialize(p: Property) -> dict:
    return {
        "property_id": p.property_id,
        "property_type": p.property_type,
        "normalized_type": p.normalized_type,
        "sub_type": p.sub_type,
        "location": p.location,
        "area_sqft": p.area_sqft,
        "current_estimated_value_inr": p.current_estimated_value_inr,
        "current_estimated_value_display": format_inr(p.current_estimated_value_inr),
        "purchase_price_inr": p.purchase_price_inr,
        "purchase_price_display": format_inr(p.purchase_price_inr),
        "annual_rent_inr": p.annual_rent_inr,
        "annual_rent_display": format_inr(p.annual_rent_inr),
        "occupancy_status": p.occupancy_status,
        "tenant_status": p.tenant_status,
    }


# --- 1. search_properties ---------------------------------------------------

def search_properties(
    db: Session,
    owner: str,
    property_type: Optional[str] = None,
    location_contains: Optional[str] = None,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
    occupancy_status: Optional[str] = None,
) -> dict:
    """Filtered lookup. property_type is matched against normalized_type
    (Retail/Office/Residential) so 'Commercial Office' rows are found under 'Office' too."""
    q = _base_query(db, owner)
    if property_type:
        q = q.filter(Property.normalized_type == normalize_type(property_type))
    if location_contains:
        q = q.filter(Property.location.ilike(f"%{location_contains}%"))
    if min_value is not None:
        q = q.filter(Property.current_estimated_value_inr >= min_value)
    if max_value is not None:
        q = q.filter(Property.current_estimated_value_inr <= max_value)
    if occupancy_status:
        q = q.filter(Property.occupancy_status == occupancy_status)

    results = [_serialize(p) for p in q.all()]
    return {"count": len(results), "properties": results}


# --- 2. portfolio_summary ---------------------------------------------------

def portfolio_summary(db: Session, owner: str) -> dict:
    """Total value, count, and a breakdown by normalized type, plus overall yield."""
    props = _base_query(db, owner).all()
    total_value = sum(p.current_estimated_value_inr or 0 for p in props)
    total_rent = sum(p.annual_rent_inr or 0 for p in props)

    by_type = {}
    for p in props:
        t = p.normalized_type
        by_type.setdefault(t, {"count": 0, "value_inr": 0, "rent_inr": 0})
        by_type[t]["count"] += 1
        by_type[t]["value_inr"] += p.current_estimated_value_inr or 0
        by_type[t]["rent_inr"] += p.annual_rent_inr or 0

    for t, agg in by_type.items():
        agg["share_of_value_pct"] = round(100 * agg["value_inr"] / total_value, 1) if total_value else 0
        agg["value_display"] = format_inr(agg["value_inr"])
        agg["rent_display"] = format_inr(agg["rent_inr"])

    return {
        "property_count": len(props),
        "total_value_inr": total_value,
        "total_value_display": format_inr(total_value),
        "total_annual_rent_inr": total_rent,
        "total_annual_rent_display": format_inr(total_rent),
        "overall_yield_pct": round(100 * total_rent / total_value, 2) if total_value else 0,
        "by_type": by_type,
        "vacant_count": sum(1 for p in props if p.occupancy_status == "Vacant"),
        "tenanted_count": sum(1 for p in props if p.occupancy_status == "Tenanted"),
        "self_occupied_count": sum(1 for p in props if p.occupancy_status == "Self-occupied"),
    }


# --- 3. portfolio_metric -----------------------------------------------------

def portfolio_metric(
    db: Session,
    owner: str,
    metric: str,
    field: str = "current_estimated_value_inr",
    group_by: Optional[str] = None,   # "normalized_type" | "location" | None
    property_type: Optional[str] = None,
    location_contains: Optional[str] = None,
) -> dict:
    """A single aggregate (sum/avg/max/min/count) of `field`, optionally grouped."""
    metric = metric.lower()
    if metric not in VALID_METRICS:
        return {"error": f"metric must be one of {sorted(VALID_METRICS)}"}
    if field not in VALID_FIELDS:
        return {"error": f"field must be one of {sorted(VALID_FIELDS)}"}

    q = _base_query(db, owner)
    if property_type:
        q = q.filter(Property.normalized_type == normalize_type(property_type))
    if location_contains:
        q = q.filter(Property.location.ilike(f"%{location_contains}%"))
    props = q.all()

    if not props:
        return {"metric": metric, "field": field, "result": None, "note": "no matching properties"}

    if metric in {"max", "min"} and not group_by:
        # return the whole property row, not just the number, so the agent can name it
        chosen = (max if metric == "max" else min)(props, key=lambda p: getattr(p, field) or 0)
        return {"metric": metric, "field": field, "property": _serialize(chosen)}

    def agg(values):
        if metric == "sum":
            return sum(values)
        if metric == "avg":
            return round(sum(values) / len(values), 2) if values else 0
        if metric == "count":
            return len(values)
        if metric == "max":
            return max(values) if values else None
        if metric == "min":
            return min(values) if values else None

    is_money = field in {"current_estimated_value_inr", "annual_rent_inr"}

    if group_by in {"normalized_type", "location"}:
        groups = {}
        for p in props:
            key = p.normalized_type if group_by == "normalized_type" else p.location
            groups.setdefault(key, []).append(getattr(p, field) or 0)
        result = {k: agg(v) for k, v in groups.items()}
        out = {"metric": metric, "field": field, "group_by": group_by, "result": result}
        if is_money and metric != "count":
            out["result_display"] = {k: format_inr(v) for k, v in result.items()}
        return out

    values = [getattr(p, field) or 0 for p in props]
    result = agg(values)
    out = {"metric": metric, "field": field, "result": result}
    if is_money and metric != "count":
        out["result_display"] = format_inr(result)
    return out


# --- 4. compare_segments -----------------------------------------------------

def compare_segments(db: Session, owner: str, segment_a: str, segment_b: str) -> dict:
    """Compare two normalized_type segments (e.g. Retail vs Residential) side by side."""
    def segment_stats(label: str) -> dict:
        norm = normalize_type(label)
        props = _base_query(db, owner).filter(Property.normalized_type == norm).all()
        value = sum(p.current_estimated_value_inr or 0 for p in props)
        rent = sum(p.annual_rent_inr or 0 for p in props)
        return {
            "segment": norm,
            "count": len(props),
            "total_value_inr": value,
            "total_value_display": format_inr(value),
            "total_annual_rent_inr": rent,
            "total_annual_rent_display": format_inr(rent),
            "yield_pct": round(100 * rent / value, 2) if value else 0,
        }

    return {"a": segment_stats(segment_a), "b": segment_stats(segment_b)}


# --- 5. hypothetical_recompute ----------------------------------------------

def hypothetical_recompute(
    db: Session,
    owner: str,
    exclude_property_ids: Optional[list] = None,
    value_overrides: Optional[dict] = None,   # {property_id: new_value_inr}
) -> dict:
    """Recomputes portfolio_summary-style aggregates with in-memory adjustments.
    Never writes to the database — purely a what-if calculation."""
    exclude_property_ids = set(exclude_property_ids or [])
    value_overrides = value_overrides or {}

    props = _base_query(db, owner).all()
    kept = [p for p in props if p.property_id not in exclude_property_ids]

    total_value = 0
    total_rent = 0
    by_type = {}
    for p in kept:
        value = value_overrides.get(p.property_id, p.current_estimated_value_inr or 0)
        rent = p.annual_rent_inr or 0
        total_value += value
        total_rent += rent
        t = p.normalized_type
        by_type.setdefault(t, {"count": 0, "value_inr": 0, "rent_inr": 0})
        by_type[t]["count"] += 1
        by_type[t]["value_inr"] += value
        by_type[t]["rent_inr"] += rent

    for t, agg in by_type.items():
        agg["value_display"] = format_inr(agg["value_inr"])
        agg["rent_display"] = format_inr(agg["rent_inr"])

    return {
        "excluded_property_ids": list(exclude_property_ids),
        "value_overrides_applied": value_overrides,
        "property_count": len(kept),
        "total_value_inr": total_value,
        "total_value_display": format_inr(total_value),
        "total_annual_rent_inr": total_rent,
        "total_annual_rent_display": format_inr(total_rent),
        "overall_yield_pct": round(100 * total_rent / total_value, 2) if total_value else 0,
        "by_type": by_type,
        "is_hypothetical": True,
    }


# --- 6. create_property ------------------------------------------------------

REQUIRED_CREATE_FIELDS = ["property_type", "location", "area_sqft", "current_estimated_value_inr"]


def create_property(db: Session, owner: str, fields: dict) -> dict:
    """Validates required fields are present before writing. Caller (agent) is
    responsible for asking the user for anything reported missing here."""
    missing = [f for f in REQUIRED_CREATE_FIELDS if not fields.get(f)]
    if missing:
        return {"created": False, "missing_fields": missing}

    # generate next property_id
    existing_ids = [p.property_id for p in db.query(Property.property_id).all()]
    next_num = max([int(pid[1:]) for pid in existing_ids if pid.startswith("P") and pid[1:].isdigit()], default=0) + 1
    new_id = f"P{next_num:03d}"

    prop = Property(
        property_id=new_id,
        user_id=owner,
        property_type=fields["property_type"],
        normalized_type=normalize_type(fields["property_type"]),
        sub_type=fields.get("sub_type"),
        location=fields["location"],
        area_sqft=fields.get("area_sqft"),
        current_estimated_value_inr=fields["current_estimated_value_inr"],
        purchase_price_inr=fields.get("purchase_price_inr"),
        annual_rent_inr=fields.get("annual_rent_inr", 0),
        occupancy_status=fields.get("occupancy_status", "Vacant"),
        tenant_status=fields.get("tenant_status", "No"),
        ownership_percent=fields.get("ownership_percent", 100),
        status="Active",
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return {"created": True, "property": _serialize(prop)}


# --- 7. update_property -------------------------------------------------------

UPDATABLE_FIELDS = {
    "property_type", "sub_type", "location", "area_sqft",
    "current_estimated_value_inr", "purchase_price_inr", "annual_rent_inr",
    "occupancy_status", "tenant_status",
}


def update_property(db: Session, owner: str, property_id: str, field: str, new_value) -> dict:
    """Updates a single field on a property the user owns. Returns an error if the
    property doesn't exist, belongs to someone else, or the field isn't updatable."""
    if field not in UPDATABLE_FIELDS:
        return {"updated": False, "error": f"field must be one of {sorted(UPDATABLE_FIELDS)}"}

    prop = db.query(Property).filter(
        Property.property_id == property_id, Property.user_id == owner
    ).first()
    if not prop:
        return {"updated": False, "error": f"no property {property_id} found for this user"}

    setattr(prop, field, new_value)
    if field == "property_type":
        prop.normalized_type = normalize_type(new_value)

    db.commit()
    db.refresh(prop)
    return {"updated": True, "property": _serialize(prop)}
