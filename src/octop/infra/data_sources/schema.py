"""Schema model for data sources: introspection output, cache, prompt scope.

A ``SchemaDoc`` is the ordered list of tables discovered via ``information_schema``
(or a user-supplied allowlist). It is cached as ``tables_json`` on
``data_source_schemas`` and rendered into the NL2SQL prompt only for the tables a
query is allowed to touch (allowlist doubles as schema scope — see design §7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from octop.infra.db.repos.data_sources import AllowedTable, Annotation


@dataclass(frozen=True)
class ColumnDoc:
    name: str
    data_type: str
    comment: str = ""
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "comment": self.comment,
            "nullable": self.nullable,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ColumnDoc:
        return cls(
            name=str(raw.get("name") or ""),
            data_type=str(raw.get("data_type") or ""),
            comment=str(raw.get("comment") or ""),
            nullable=bool(raw.get("nullable", True)),
        )


@dataclass(frozen=True)
class TableDoc:
    name: str
    columns: list[ColumnDoc] = field(default_factory=list)
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "comment": self.comment,
            "columns": [c.to_dict() for c in self.columns],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TableDoc:
        cols = raw.get("columns") or []
        return cls(
            name=str(raw.get("name") or ""),
            comment=str(raw.get("comment") or ""),
            columns=[ColumnDoc.from_dict(c) for c in cols if isinstance(c, dict)],
        )


SchemaDoc = list[TableDoc]


def serialize_schema(tables: SchemaDoc) -> str:
    return json.dumps([t.to_dict() for t in tables], ensure_ascii=False)


def deserialize_schema(tables_json: str) -> SchemaDoc:
    try:
        raw = json.loads(tables_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return [TableDoc.from_dict(item) for item in raw if isinstance(item, dict)]


def _annotation_map(annotations: list[Annotation]) -> dict[tuple[str, str], str]:
    """Index notes by ``(table, column)``; table-scope notes use empty column."""
    out: dict[tuple[str, str], str] = {}
    for ann in annotations:
        if ann.scope == "table":
            out[(ann.table_name, "")] = ann.note
        elif ann.scope == "column":
            out[(ann.table_name, ann.column_name)] = ann.note
    return out


def scope_schema(
    schema: SchemaDoc,
    allowlist: list[AllowedTable],
    *,
    annotations: list[Annotation] | None = None,
) -> SchemaDoc:
    """Filter the cached schema to allowlisted tables/columns (schema scope).

    A table with an empty column list means "all columns of that table allowed".
    Business-caliber annotations override raw column comments (accuracy lever).
    """
    ann_map = _annotation_map(annotations or [])
    by_name = {t.name: t for t in schema}
    scoped: SchemaDoc = []
    for item in allowlist:
        table = by_name.get(item.table_name)
        if table is None:
            # Allowlist may reference a table not (yet) in cache; keep a stub so
            # the guard still recognizes it, with just the explicit columns.
            cols = [ColumnDoc(name=c, data_type="", comment="") for c in item.columns]
            scoped.append(
                TableDoc(
                    name=item.table_name,
                    columns=cols,
                    comment=ann_map.get((item.table_name, ""), ""),
                )
            )
            continue
        wanted = set(item.columns)
        columns = [c for c in table.columns if not wanted or c.name in wanted]
        columns = [
            ColumnDoc(
                name=c.name,
                data_type=c.data_type,
                comment=ann_map.get((table.name, c.name)) or c.comment,
                nullable=c.nullable,
            )
            for c in columns
        ]
        scoped.append(
            TableDoc(
                name=table.name,
                columns=columns,
                comment=ann_map.get((table.name, "")) or table.comment,
            )
        )
    return scoped


def render_schema_for_prompt(schema: SchemaDoc, *, dialect: str) -> str:
    """Render a compact, LLM-facing schema block (columns + types + caliber)."""
    lines: list[str] = [f"# Database dialect: {dialect}"]
    for table in schema:
        header = f"TABLE {table.name}"
        if table.comment:
            header += f"  -- {table.comment}"
        lines.append(header)
        for col in table.columns:
            piece = f"  {col.name} {col.data_type}".rstrip()
            note = col.comment
            if note:
                piece += f"  -- {note}"
            lines.append(piece)
    return "\n".join(lines)
