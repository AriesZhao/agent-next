"""In-memory data store with seed data loading."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

_STORE: dict[str, list[dict[str, Any]]] = {
    "orders": [],
    "products": [],
    "customers": [],
}


def _seed_id() -> str:
    return uuid.uuid4().hex[:12]


def load_seed(path: Path) -> None:
    """Load seed data from JSON file into the store."""
    if not path.exists():
        _init_defaults()
        return
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    for key in _STORE:
        if key in data:
            _STORE[key] = data[key]
    if not any(_STORE.values()):
        _init_defaults()


def _init_defaults() -> None:
    """Populate with sample data when no seed file exists."""
    _STORE["customers"] = [
        {"id": "cust_001", "name": "Alice", "email": "alice@example.com"},
        {"id": "cust_002", "name": "Bob", "email": "bob@example.com"},
    ]
    _STORE["products"] = [
        {
            "id": "prod_001",
            "name": "Widget A",
            "price": 29.99,
            "in_stock": True,
            "stock_count": 150,
        },
        {
            "id": "prod_002",
            "name": "Widget B",
            "price": 49.99,
            "in_stock": True,
            "stock_count": 42,
        },
        {
            "id": "prod_003",
            "name": "Gadget C",
            "price": 99.00,
            "in_stock": False,
            "stock_count": 0,
        },
    ]
    _STORE["orders"] = [
        {
            "id": "ord_001",
            "customer": "Alice",
            "items": [{"product_id": "prod_001", "qty": 2}],
            "amount": 59.98,
            "status": "completed",
        },
        {
            "id": "ord_002",
            "customer": "Bob",
            "items": [{"product_id": "prod_002", "qty": 1}],
            "amount": 49.99,
            "status": "pending",
        },
        {
            "id": "ord_003",
            "customer": "Alice",
            "items": [{"product_id": "prod_001", "qty": 1}, {"product_id": "prod_002", "qty": 1}],
            "amount": 79.98,
            "status": "shipped",
        },
    ]


def get_collection(name: str) -> list[dict[str, Any]]:
    return _STORE.get(name, [])


def find_one(name: str, predicate: Any) -> dict[str, Any] | None:
    for item in _STORE.get(name, []):
        if predicate(item):
            return item
    return None


def add_one(name: str, item: dict[str, Any]) -> dict[str, Any]:
    if "id" not in item:
        item["id"] = _seed_id()
    _STORE.setdefault(name, []).append(item)
    return item


def reset() -> None:
    _init_defaults()
