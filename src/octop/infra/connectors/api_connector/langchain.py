"""LangChain StructuredTool generation for API Connector."""

from __future__ import annotations

from typing import Any

from harness_agent.mcp import mcp_args_model, sanitize_llm_tool_name

from octop.infra.connectors.api_connector.adapter import call_tool


def build_api_connector_langchain_tools(
    *,
    connector_name: str,
    connector_def: dict[str, Any],
    http_client: Any | None = None,
) -> list[Any]:
    from langchain_core.tools import StructuredTool

    out: list[Any] = []
    display_name = connector_def.get("display_name", connector_name)

    for tool_def in connector_def.get("tools", []):
        raw_name = tool_def["name"]
        fqn = f"{connector_name}__{raw_name}"
        lc_name = sanitize_llm_tool_name(fqn)
        description = f"[{display_name}] {tool_def.get('description', raw_name)}"

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in tool_def.get("parameters", {}).items():
            prop: dict[str, Any] = {
                "type": param.get("type", "string"),
                "description": param.get("description", ""),
            }
            if param.get("enum"):
                prop["enum"] = param["enum"]
            if param.get("type") == "array" and param.get("items"):
                prop["items"] = param["items"]
            if param.get("type") == "object" and param.get("properties"):
                prop["properties"] = param["properties"]
            properties[param_name] = prop
            if param.get("required"):
                required.append(param_name)

        input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        client = http_client

        def _make_fn(cn: str, tn: str, cdef: dict[str, Any], c: Any) -> Any:
            def _fn(**kwargs: Any) -> str:
                cleaned = {k: v for k, v in kwargs.items() if v is not None}
                result = call_tool(cn, tn, cdef, cleaned, client=c)
                if "error" in result:
                    return str(result.get("message", result["error"]))
                return str(result.get("data", ""))

            return _fn

        out.append(
            StructuredTool.from_function(
                func=_make_fn(connector_name, raw_name, connector_def, client),
                name=lc_name,
                description=description,
                args_schema=mcp_args_model(lc_name, input_schema),
            )
        )
    return out
