"""Business API — orders, products, customers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..data import add_one, find_one, get_collection

router = APIRouter(tags=["business"])


# ── Orders ────────────────────────────────────────────────────────────────────


class CreateOrderBody(BaseModel):
    customer: str = Field(description="Customer name")
    items: list[dict[str, Any]] = Field(default_factory=list, description="Order line items")
    amount: float = Field(description="Order total amount")


@router.get("/orders", summary="List orders")
def list_orders(
    status: str | None = Query(default=None, description="Filter by status"),
    limit: int = Query(default=10, ge=1, le=100, description="Max results"),
) -> list[dict[str, Any]]:
    orders = get_collection("orders")
    if status:
        orders = [o for o in orders if o.get("status") == status]
    return orders[:limit]


@router.get("/orders/{order_id}", summary="Get order detail")
def get_order(order_id: str) -> dict[str, Any]:
    order = find_one("orders", lambda o: o["id"] == order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders", status_code=201, summary="Create order")
def create_order(body: CreateOrderBody) -> dict[str, Any]:
    return add_one("orders", body.model_dump())


# ── Products ──────────────────────────────────────────────────────────────────


@router.get("/products", summary="List products")
def list_products(
    in_stock: bool | None = Query(default=None, description="Filter by stock availability"),
) -> list[dict[str, Any]]:
    products = get_collection("products")
    if in_stock is not None:
        products = [p for p in products if p.get("in_stock") == in_stock]
    return products


@router.get("/products/{product_id}", summary="Get product detail")
def get_product(product_id: str) -> dict[str, Any]:
    product = find_one("products", lambda p: p["id"] == product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ── Customers ─────────────────────────────────────────────────────────────────


@router.get("/customers", summary="List customers")
def list_customers() -> list[dict[str, Any]]:
    return get_collection("customers")


@router.get("/customers/{customer_id}", summary="Get customer detail")
def get_customer(customer_id: str) -> dict[str, Any]:
    customer = find_one("customers", lambda c: c["id"] == customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer
