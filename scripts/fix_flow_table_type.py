"""Replace every `Table` type reference with `DataFrame` in flow JSONs.

The bundled OpenRAG flows were built when Langflow exposed a `Table` type.
Current Langflow (`lfx.schema.dataframe` in the image) no longer has it —
the class was effectively renamed to `DataFrame`. When Langflow tries to
resolve `Table` as an import path (via edges with `"type": "Table"`, or
component `base_classes: ["Table"]`), it raises:

    ModuleNotFoundError: No module named 'lfx.schema.dataframe.Table';
    'lfx.schema.dataframe' is not a package

This script rewrites `flows/*.json` so every `Table` type identifier
becomes `DataFrame`. Specifically:

* List members under `input_types`, `output_types`, `inputTypes`,
  `outputTypes`, `base_classes`: `"Table"` → `"DataFrame"` (dedup).
* Scalar string under `selected`, `type`, `base_class`: if the value is
  exactly `"Table"`, replace with `"DataFrame"`.

All other strings (code bodies, labels, descriptions) are untouched.
Idempotent and safe to re-run.

Usage:
    python scripts/fix_flow_table_type.py           # dry-run
    python scripts/fix_flow_table_type.py --write   # apply
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

STALE = "Table"
NEW = "DataFrame"

LIST_KEYS = {
    "input_types",
    "output_types",
    "inputTypes",
    "outputTypes",
    "base_classes",
    "types",
}
SCALAR_KEYS = {"selected", "type", "base_class"}
CODE_KEYS = {"code", "value"}  # only transformed when value looks like Python source


HANDLE_KEYS = {"sourceHandle", "targetHandle"}
ORNATE = "œ"  # Langflow's quote substitute in encoded handle IDs


def _walk(obj: Any, counter: dict[str, int]) -> Any:
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in LIST_KEYS and isinstance(v, list):
                new_list: list[object] = []
                changed = False
                for item in v:
                    if item == STALE:
                        changed = True
                        counter["list"] += 1
                        if NEW not in new_list:
                            new_list.append(NEW)
                    else:
                        new_list.append(item)
                if changed:
                    obj[k] = new_list
            elif k in SCALAR_KEYS and isinstance(v, str) and v == STALE:
                obj[k] = NEW
                counter["scalar"] += 1
            elif k in HANDLE_KEYS and isinstance(v, str) and ORNATE in v:
                new_val, n = _rewrite_handle(v)
                if n:
                    obj[k] = new_val
                    counter["handle"] += n
            elif k == "id" and isinstance(v, str) and ORNATE in v and STALE in v:
                # Edge IDs embed the handle JSON twice; same rewrite applies.
                new_val, n = _rewrite_handle(v)
                if n:
                    obj[k] = new_val
                    counter["handle"] += n
            elif (
                k in CODE_KEYS
                and isinstance(v, str)
                and STALE in v
                and ("input_types=[" in v or "lfx.schema.dataframe" in v)
            ):
                new_val, n = _rewrite_code(v)
                if n:
                    obj[k] = new_val
                    counter["code"] = counter.get("code", 0) + n
            else:
                _walk(v, counter)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, counter)
    return obj


def _rewrite_code(s: str) -> tuple[str, int]:
    """Rewrite Python source: string literals AND standalone `Table` identifiers.

    Leaves `TableInput`, `TableSchema`, `table_icon` etc. alone (legitimate
    Langflow classes / attribute names).
    """
    import re

    total = 0

    # 1) String literals: "Table" / 'Table'
    s, n1 = re.subn(
        r"(?<![A-Za-z_])([\"'])Table\1(?![A-Za-z_])",
        lambda m: f"{m.group(1)}DataFrame{m.group(1)}",
        s,
    )
    total += n1

    # 2) Bare Python identifier `Table` — only when not part of a longer name.
    #    Matches: `import Table`, `-> Table:`, `Table(args)`, `: Table`, etc.
    #    Skips: `TableInput`, `_Table`, `SomeTable`.
    s, n2 = re.subn(
        r"(?<![A-Za-z_0-9])Table(?![A-Za-z_0-9])",
        "DataFrame",
        s,
    )
    total += n2
    return s, total


def _rewrite_handle(s: str) -> tuple[str, int]:
    """Decode Langflow's ornate-quote handle, replace Table→DataFrame, re-encode."""
    try:
        # Find each `{...}` block (they are JSON with `œ` as quote char),
        # decode, rewrite, re-encode.
        import re

        count = 0

        def repl(match: "re.Match[str]") -> str:
            nonlocal count
            block = match.group(0)
            decoded = block.replace(ORNATE, '"')
            try:
                parsed = json.loads(decoded)
            except json.JSONDecodeError:
                return block
            sub_counter = {"list": 0, "scalar": 0, "handle": 0}
            _walk(parsed, sub_counter)
            if sub_counter["list"] == 0 and sub_counter["scalar"] == 0:
                return block
            count += sub_counter["list"] + sub_counter["scalar"]
            rebuilt = json.dumps(parsed, separators=(",", ":"))
            return rebuilt.replace('"', ORNATE)

        out = re.sub(r"\{[^{}]*\}", repl, s)
        return out, count
    except Exception:
        return s, 0


def patch_file(path: Path, *, write: bool) -> dict[str, int]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    counter = {"list": 0, "scalar": 0, "handle": 0, "code": 0}
    _walk(data, counter)
    total = sum(counter.values())
    if total and write:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return counter


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="flows", help="flows directory")
    ap.add_argument("--write", action="store_true", help="apply changes")
    args = ap.parse_args()

    flows_dir = Path(args.dir)
    if not flows_dir.is_dir():
        raise SystemExit(f"[error] not a directory: {flows_dir}")

    grand = {"list": 0, "scalar": 0, "handle": 0, "code": 0}
    for fp in sorted(flows_dir.glob("*.json")):
        c = patch_file(fp, write=args.write)
        if any(c.values()):
            flag = "(would rename)" if not args.write else "(renamed)"
            print(
                f"  {flag:18s} list={c['list']:3d} scalar={c['scalar']:3d} "
                f"handle={c['handle']:3d} code={c['code']:3d}  {fp.name}"
            )
            for key in grand:
                grand[key] += c[key]

    mode = "DRY-RUN" if not args.write else "WRITE"
    print(
        f"[{mode}] totals: list={grand['list']}, scalar={grand['scalar']}, "
        f"handle={grand['handle']}, code={grand['code']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
