"""Schema scope + prompt rendering tests (§7, §8.1)."""

from __future__ import annotations

from octop.infra.data_sources.schema import (
    ColumnDoc,
    TableDoc,
    deserialize_schema,
    render_schema_for_prompt,
    scope_schema,
    serialize_schema,
)
from octop.infra.db.repos.data_sources import AllowedTable, Annotation


def _sample():
    return [
        TableDoc(
            "orders",
            [
                ColumnDoc("id", "int", ""),
                ColumnDoc("amount", "numeric", "raw comment"),
                ColumnDoc("secret", "text", ""),
            ],
        ),
        TableDoc("users", [ColumnDoc("id", "int", ""), ColumnDoc("name", "text", "")]),
        TableDoc("ignored", [ColumnDoc("x", "int", "")]),
    ]


def test_serialize_roundtrip():
    schema = _sample()
    back = deserialize_schema(serialize_schema(schema))
    assert [t.name for t in back] == ["orders", "users", "ignored"]
    assert back[0].columns[1].data_type == "numeric"


def test_deserialize_bad_json_is_empty():
    assert deserialize_schema("not-json") == []
    assert deserialize_schema("") == []


def test_scope_filters_tables_and_columns():
    allow = [AllowedTable("orders", ["id", "amount"]), AllowedTable("users", [])]
    scoped = scope_schema(_sample(), allow)
    names = {t.name for t in scoped}
    assert names == {"orders", "users"}
    orders = next(t for t in scoped if t.name == "orders")
    assert {c.name for c in orders.columns} == {"id", "amount"}
    # users has empty column list => all columns kept
    users = next(t for t in scoped if t.name == "users")
    assert {c.name for c in users.columns} == {"id", "name"}


def test_scope_annotation_overrides_column_comment():
    allow = [AllowedTable("orders", ["amount"])]
    anns = [Annotation("column", "orders", "amount", "GMV，含税口径")]
    scoped = scope_schema(_sample(), allow, annotations=anns)
    orders = scoped[0]
    assert orders.columns[0].comment == "GMV，含税口径"


def test_scope_table_annotation():
    allow = [AllowedTable("orders", ["id"])]
    anns = [Annotation("table", "orders", "", "订单主表")]
    scoped = scope_schema(_sample(), allow, annotations=anns)
    assert scoped[0].comment == "订单主表"


def test_scope_stub_for_unknown_table():
    allow = [AllowedTable("newtable", ["a", "b"])]
    scoped = scope_schema(_sample(), allow)
    assert scoped[0].name == "newtable"
    assert {c.name for c in scoped[0].columns} == {"a", "b"}


def test_render_prompt_includes_dialect_and_columns():
    scoped = scope_schema(_sample(), [AllowedTable("orders", ["amount"])])
    text = render_schema_for_prompt(scoped, dialect="postgres")
    assert "postgres" in text
    assert "orders" in text
    assert "amount" in text
