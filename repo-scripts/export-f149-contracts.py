#!/usr/bin/env python3
"""从 F149 三个唯一 Gateway 事实源导出 deterministic types-only OpenAPI 文档。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from octoagent.core.models import ActionRequestEnvelope, ActionResultEnvelope
from octoagent.gateway.main import create_app
from octoagent.gateway.routes import f149_web_contract, task_sse_contract
from octoagent.gateway.services.control_plane import action_registry

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPENAPI_VERSION = "3.1.0"


def _require_worktree_modules() -> None:
    expected_root = (_REPO_ROOT / "octoagent").resolve()
    for module in (f149_web_contract, task_sse_contract, action_registry):
        module_path = Path(module.__file__).resolve()
        if not module_path.is_relative_to(expected_root):
            raise RuntimeError(f"F149_CONTRACT_SOURCE_OUTSIDE_WORKTREE: {module_path}")


def _schema_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                refs.add(item)
            else:
                refs.update(_schema_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_schema_refs(item))
    return refs


def _prune_rest_components(document: dict[str, Any]) -> dict[str, Any]:
    source_components = document.get("components", {}).get("schemas", {})
    if not isinstance(source_components, Mapping):
        raise RuntimeError("F149_REST_COMPONENTS_INVALID")
    selected: dict[str, object] = {}
    pending = {
        ref.removeprefix("#/components/schemas/")
        for ref in _schema_refs(document.get("paths", {}))
        if ref.startswith("#/components/schemas/")
    }
    while pending:
        name = min(pending)
        pending.remove(name)
        if name in selected:
            continue
        schema = source_components.get(name)
        if not isinstance(schema, Mapping):
            raise RuntimeError(f"F149_REST_COMPONENT_MISSING: {name}")
        selected[name] = deepcopy(dict(schema))
        pending.update(
            ref.removeprefix("#/components/schemas/")
            for ref in _schema_refs(schema)
            if ref.startswith("#/components/schemas/")
            and ref.removeprefix("#/components/schemas/") not in selected
        )
    document["components"] = {"schemas": selected}
    return document


def _rewrite_defs_refs(value: object) -> object:
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return value.replace("#/$defs/", "#/components/schemas/", 1)
    if isinstance(value, dict):
        return {key: _rewrite_defs_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_defs_refs(item) for item in value]
    return value


def _add_schema(
    components: dict[str, object],
    schema: Mapping[str, object],
    *,
    fallback_name: str,
) -> None:
    candidate = deepcopy(dict(schema))
    definitions = candidate.pop("$defs", {})
    if definitions and not isinstance(definitions, Mapping):
        raise RuntimeError("F149_SCHEMA_DEFINITIONS_INVALID")
    for name, definition in sorted(dict(definitions).items()):
        if not isinstance(definition, Mapping):
            raise RuntimeError(f"F149_SCHEMA_DEFINITION_INVALID: {name}")
        _store_component(components, str(name), _rewrite_defs_refs(dict(definition)))
    title = str(candidate.get("title", "")).strip() or fallback_name
    _store_component(components, title, _rewrite_defs_refs(candidate))


def _store_component(components: dict[str, object], name: str, schema: object) -> None:
    existing = components.get(name)
    if existing is not None and existing != schema:
        raise RuntimeError(f"F149_SCHEMA_COMPONENT_COLLISION: {name}")
    components[name] = schema


async def _unused_action_handler(
    request: ActionRequestEnvelope,
) -> ActionResultEnvelope:
    del request
    raise RuntimeError("types-only exporter不得执行action handler")


def _action_openapi() -> dict[str, object]:
    handlers = {
        action_id: _unused_action_handler
        for action_id in action_registry.F149_ACTION_IDS
    }
    artifact = action_registry.export_f149_action_contract(
        action_registry.build_action_contracts(handlers)
    )
    components: dict[str, object] = {}
    for item in artifact["actions"]:
        if not isinstance(item, Mapping):
            raise RuntimeError("F149_ACTION_SCHEMA_INVALID")
        action_id = str(item["action_id"])
        for schema_key, suffix in (
            ("params_schema", "Params"),
            ("result_schema", "Result"),
        ):
            schema = item.get(schema_key)
            if not isinstance(schema, Mapping):
                raise RuntimeError(
                    f"F149_ACTION_SCHEMA_MISSING: {action_id}/{schema_key}"
                )
            _add_schema(
                components,
                schema,
                fallback_name=_component_name(action_id, suffix),
            )
    return _openapi_document("F149 Action Contracts", components)


def _component_name(action_id: str, suffix: str) -> str:
    stem = "".join(part.title() for part in action_id.replace("_", ".").split("."))
    return f"F149{stem}{suffix}"


def _sse_openapi() -> dict[str, object]:
    components: dict[str, object] = {}
    _add_schema(
        components,
        task_sse_contract.F149TaskSSEFrame.model_json_schema(),
        fallback_name="F149TaskSSEFrame",
    )
    return _openapi_document("F149 Task SSE Contracts", components)


def _openapi_document(title: str, components: dict[str, object]) -> dict[str, object]:
    return {
        "openapi": _OPENAPI_VERSION,
        "info": {"title": title, "version": "1.0.0"},
        "paths": {},
        "components": {"schemas": components},
    }


def export_contracts(output_dir: Path) -> None:
    """导出 REST/action/SSE 三份可重复文档，不生成客户端或执行网络请求。"""

    _require_worktree_modules()
    documents = {
        "rest.openapi.json": _prune_rest_components(
            f149_web_contract.export_f149_rest_openapi(create_app())
        ),
        "actions.openapi.json": _action_openapi(),
        "task-sse.openapi.json": _sse_openapi(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        rendered = json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        (output_dir / name).write_text(rendered + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    export_contracts(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
