"""
One-time loader: reads data/users.csv and data/properties.csv into portfolio.db.

Run with: python -m scripts.load_data
Safe to re-run — drops and recreates all tables first.
"""
import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine, SessionLocal, Base
from app.models import User, Property, normalize_type

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def to_int_or_none(value: str):
    value = (value or "").strip()
    return int(value) if value else None


def to_int_or_zero(value: str):
    value = (value or "").strip()
    return int(value) if value else 0


def load_users(db):
    path = os.path.join(DATA_DIR, "users.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            db.add(User(
                user_id=row["user_id"],
                name=row["name"],
                city=row["city"],
                preferences=row["preferences"],
                preferred_locations=row["preferred_locations"],
                portfolio_value_preference_inr=row["portfolio_value_preference_inr"],
            ))


def load_properties(db):
    path = os.path.join(DATA_DIR, "properties.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            db.add(Property(
                property_id=row["property_id"],
                user_id=row["user_id"],
                property_type=row["property_type"],
                normalized_type=normalize_type(row["property_type"]),
                sub_type=row["sub_type"],
                location=row["location"],
                area_sqft=to_int_or_none(row["area_sqft"]),
                current_estimated_value_inr=to_int_or_none(row["current_estimated_value_inr"]),
                purchase_price_inr=to_int_or_none(row["purchase_price_inr"]),
                annual_rent_inr=to_int_or_zero(row["annual_rent_inr"]),
                occupancy_status=row["occupancy_status"],
                tenant_status=row["tenant_status"],
                ownership_percent=to_int_or_none(row["ownership_percent"]) or 100,
                status=row["status"],
            ))


def main():
    print(f"Recreating schema at {engine.url} ...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        load_users(db)
        load_properties(db)
        db.commit()
        n_users = db.query(User).count()
        n_props = db.query(Property).count()
        print(f"Loaded {n_users} users, {n_props} properties.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
