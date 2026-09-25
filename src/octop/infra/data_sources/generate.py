"""NL2SQL generation: prompt assembly + sync chat-model call (§8.1).

The model is resolved from the instance setting ``data_source_nl2sql_model``
(a ``provider/model`` ref) via :func:`build_probe_chat_model`. Configuration is
**fail-fast**: when the ref is missing or unusable we raise
:class:`ModelNotConfigured` rather than silently falling back to another model
(a wrong-model guess produces wrong numbers, see design §8.1).

Generation runs fully synchronously (``chat_model.invoke``) inside the pipeline's
executor thread. SQL extraction tolerates code fences and prose.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from octop.infra.utils.llm_text import llm_text_content

NL2SQL_MODEL_KEY = "data_source_nl2sql_model"

# A small, dialect-neutral calibration set. Kept short to bound prompt size.
_DEFAULT_FEW_SHOT: tuple[tuple[str, str], ...] = (
    ("How many orders are there?", "SELECT COUNT(*) AS total FROM orders LIMIT 100"),
    (
        "Total amount by status",
        "SELECT status, SUM(amount) AS total FROM orders GROUP BY status ORDER BY total DESC LIMIT 100",
    ),
)


class ModelNotConfigured(Exception):
    """The NL2SQL model ref is unset or not resolvable to a chat model."""


class GenerationFailed(Exception):
    """The model returned no extractable SQL."""


def resolve_model_ref(settings_get: Callable[[str], str | None]) -> str:
    return (settings_get(NL2SQL_MODEL_KEY) or "").strip()


def _split_ref(ref: str) -> tuple[str, str]:
    provider, _, model = ref.partition("/")
    return provider.strip(), model.strip()


def is_ref_configured(ref: str) -> bool:
    provider, model = _split_ref(ref)
    return bool(provider and model)


def build_chat_model_for_ref(provider_repo: Any, ref: str) -> Any:
    """Resolve ``provider/model`` to a sync-capable chat model, fail-fast."""
    from octop.infra.agents.providers.probe import build_probe_chat_model

    provider_name, model_id = _split_ref(ref)
    if not provider_name or not model_id:
        raise ModelNotConfigured("data source NL2SQL model is not configured")
    row = provider_repo.get_by_name(provider_name) if provider_repo is not None else None
    if row is None:
        raise ModelNotConfigured(f"unknown provider: {provider_name}")
    models = row.get_models() if hasattr(row, "get_models") else []
    if not any(str(m.get("id") or "") == model_id for m in models):
        raise ModelNotConfigured(f"unknown model: {ref}")
    return build_probe_chat_model(row, model_id=model_id)


def _dialect_rules(dialect: str) -> str:
    if dialect == "mysql":
        return (
            "- Dialect: MySQL. Use backticks for identifiers when needed.\n"
            "- Always include a LIMIT clause."
        )
    return (
        "- Dialect: PostgreSQL. Use double quotes for identifiers only if needed.\n"
        "- Always include a LIMIT clause."
    )


def build_prompt(
    question: str,
    *,
    schema_text: str,
    dialect: str,
    few_shot: Sequence[tuple[str, str]] | None = None,
    previous_error: str | None = None,
) -> str:
    shots = few_shot if few_shot is not None else _DEFAULT_FEW_SHOT
    parts: list[str] = [
        "You translate a natural-language question into ONE read-only SQL query.",
        "Rules:",
        "- Use only the tables and columns shown below.",
        "- Emit a single SELECT statement (CTEs and subqueries are allowed).",
        "- No DML, DDL, or access to system catalogs.",
        _dialect_rules(dialect),
        "- Prefer explicit columns; add ORDER BY when many rows are expected.",
        "",
        "Schema:",
        schema_text.strip(),
        "",
        "Examples:",
    ]
    for q, sql in shots:
        parts.append(f"Q: {q}\nSQL: {sql}")
    if previous_error:
        parts += ["", f"The previous attempt failed: {previous_error}", "Correct it."]
    parts += ["", f"Q: {question}", "SQL:"]
    return "\n".join(parts)


def extract_sql(text: str) -> str:
    """Pull a single SQL statement out of a model reply (fence / prose tolerant)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    fenced = _first_fenced_block(raw)
    candidate = fenced if fenced else raw
    candidate = candidate.strip()
    # Drop a leading "SQL:" label the model may echo.
    for prefix in ("SQL:", "sql:"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :].strip()
    # Cut at the first blank line if prose leaked in (only when not fenced).
    if not fenced:
        candidate = candidate.split("\n\n", 1)[0]
    # Keep only up to the first statement terminator.
    candidate = candidate.split(";", 1)[0].strip()
    if _looks_like_query(candidate):
        return candidate
    return ""


def _looks_like_query(text: str) -> bool:
    head = text.lower().lstrip()
    return head.startswith(("select", "with", "(", "(with"))


def _first_fenced_block(text: str) -> str | None:
    marker = "```"
    if marker not in text:
        return None
    after = text.split(marker, 1)[1]
    # optional language tag line
    if after.lower().startswith("sql"):
        after = after[3:]
    block = after.split(marker, 1)[0]
    return block.strip() or None


def generate_sql(
    question: str,
    *,
    schema_text: str,
    dialect: str,
    chat_model: Any,
    few_shot: Sequence[tuple[str, str]] | None = None,
    previous_error: str | None = None,
) -> str:
    prompt = build_prompt(
        question,
        schema_text=schema_text,
        dialect=dialect,
        few_shot=few_shot,
        previous_error=previous_error,
    )
    result = chat_model.invoke(prompt)
    text = llm_text_content(result)
    sql = extract_sql(text)
    if not sql:
        raise GenerationFailed("model returned no SQL")
    return sql
