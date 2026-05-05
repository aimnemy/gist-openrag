"""Replace the bundled EmbeddingModel component's `code.value` with the
current (0.4.x) implementation shipped in Langflow's own library.

The bundled flow's component code imports two symbols removed from
`lfx.base.models.unified_models` in 0.4.x:

    handle_model_input_update     # removed
    get_embeddings                # removed

Surgical replacements for both exist (`update_model_options_in_build_config`
and `get_embedding_class` + provider-specific instantiation), but the whole
`build_embeddings` body changed substantially. Cleanest fix: drop-in the
current source from:

    lfx/components/models_and_agents/embedding_model.py

This keeps the public component contract (name, inputs, output) identical,
so the existing flow edges stay valid.

Usage:
    python scripts/fix_embedding_component.py \\
        --source /tmp/new_embedding_model.py --write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OLD_COMPONENT_TYPES = {"EmbeddingModel"}  # override via --type on CLI


def find_embedding_nodes(flow: dict) -> list[dict]:
    hits: list[dict] = []
    for node in flow.get("data", {}).get("nodes", []):
        if node.get("data", {}).get("type") in OLD_COMPONENT_TYPES:
            hits.append(node)
    return hits


def patch_flow(path: Path, new_code: str, *, write: bool) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = find_embedding_nodes(data)
    if not nodes:
        return 0
    changed = 0
    for node in nodes:
        template = node["data"]["node"]["template"]
        code_entry = template.get("code")
        if not isinstance(code_entry, dict) or "value" not in code_entry:
            continue
        if code_entry["value"] == new_code:
            continue
        code_entry["value"] = new_code
        changed += 1
    if changed and write:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="new component .py file")
    ap.add_argument("--dir", default="flows", help="flows directory")
    ap.add_argument(
        "--type",
        default="EmbeddingModel",
        help="node data.type to match (e.g. EmbeddingModel, Agent)",
    )
    ap.add_argument("--write", action="store_true", help="apply changes")
    args = ap.parse_args()

    global OLD_COMPONENT_TYPES
    OLD_COMPONENT_TYPES = {args.type}

    new_code = Path(args.source).read_text(encoding="utf-8")
    if "handle_model_input_update" in new_code or (
        "get_embeddings" in new_code and "get_embedding_class" not in new_code
    ):
        raise SystemExit("[error] source file still references removed symbols")

    total = 0
    for fp in sorted(Path(args.dir).glob("*.json")):
        n = patch_flow(fp, new_code, write=args.write)
        if n:
            flag = "(would replace)" if not args.write else "(replaced)"
            print(f"  {flag:18s} {n} {args.type} node(s)  {fp.name}")
            total += n
    mode = "DRY-RUN" if not args.write else "WRITE"
    print(f"[{mode}] total {args.type} replacements: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
