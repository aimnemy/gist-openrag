"""One-shot mirror of BeyondKM Weaviate → OpenRAG OpenSearch.

Reads documents from BeyondKM's `DOCUMENTS` collection (Weaviate, host
8085) and indexes them into OpenRAG's `documents` index (OpenSearch, host
9200). Uses REST-only clients so there are no heavy SDK deps — a stdlib
install + `requests` is enough.

Usage:
    uv run --with requests python scripts/sync_beyondkm_to_openrag.py --limit 50
    uv run --with requests python scripts/sync_beyondkm_to_openrag.py --dry-run
    uv run --with requests python scripts/sync_beyondkm_to_openrag.py   # all docs
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Iterator

import requests  # type: ignore[import-untyped]
from requests.auth import HTTPBasicAuth

WEAVIATE_URL = os.environ.get("WEAVIATE_URL", "http://localhost:8085")
WEAVIATE_FIELDS = [
    "content",
    "source",
    "filename",
    "workspace",
    "chunk_id",
    "chunk_type",
    "doc_name",
    "heading_path",
    "content_category",
    "page_number",
]

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "https://localhost:9200")
OPENSEARCH_USER = os.environ.get("OPENSEARCH_USERNAME", "admin")
OPENSEARCH_PASSWORD = os.environ.get("OPENSEARCH_PASSWORD", "")


def iter_weaviate(
    collection: str, page_size: int = 100, limit: int | None = None
) -> Iterator[dict[str, Any]]:
    """Yield objects from Weaviate via REST pagination (after + limit)."""
    props = " ".join(WEAVIATE_FIELDS)
    query_tmpl = """
    {{
      Get {{
        {cls}(limit: {ps}{after}) {{
          _additional {{ id }}
          {props}
        }}
      }}
    }}
    """
    after_clause = ""
    fetched = 0
    while True:
        body = {
            "query": query_tmpl.format(
                cls=collection, ps=page_size, after=after_clause, props=props
            )
        }
        resp = requests.post(
            f"{WEAVIATE_URL}/v1/graphql", json=body, timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        objs = data.get("data", {}).get("Get", {}).get(collection, []) or []
        if not objs:
            return
        # Capture cursor BEFORE yielding — the caller mutates `_additional`.
        last_id = objs[-1].get("_additional", {}).get("id")
        for obj in objs:
            yield obj
            fetched += 1
            if limit is not None and fetched >= limit:
                return
        if not last_id:
            return
        after_clause = f', after: "{last_id}"'


def ensure_os_index(session: requests.Session, index: str) -> None:
    """Create the target OpenSearch index if it doesn't exist."""
    url = f"{OPENSEARCH_URL}/{index}"
    head = session.head(url, verify=False, timeout=10)
    if head.status_code == 200:
        return
    mapping = {
        "mappings": {
            "properties": {
                "content": {"type": "text"},
                "source": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "workspace": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "chunk_type": {"type": "keyword"},
                "doc_name": {"type": "keyword"},
                "heading_path": {"type": "text"},
                "content_category": {"type": "keyword"},
                "page_number": {"type": "integer"},
                "_beyondkm_id": {"type": "keyword"},
            }
        }
    }
    r = session.put(url, json=mapping, verify=False, timeout=15)
    r.raise_for_status()


def bulk_index(
    session: requests.Session, index: str, batch: list[dict[str, Any]]
) -> int:
    """Push one _bulk payload to OpenSearch. Returns # indexed."""
    if not batch:
        return 0
    import json

    lines: list[str] = []
    for doc in batch:
        doc_id = doc.pop("_beyondkm_id")
        lines.append(json.dumps({"index": {"_index": index, "_id": doc_id}}))
        lines.append(json.dumps(doc))
    payload = "\n".join(lines) + "\n"
    r = session.post(
        f"{OPENSEARCH_URL}/_bulk",
        data=payload,
        headers={"Content-Type": "application/x-ndjson"},
        verify=False,
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        errs = [it for it in body["items"] if next(iter(it.values())).get("error")]
        if errs:
            print(f"[warn] {len(errs)} items had errors; first: {errs[0]}", file=sys.stderr)
    return len(batch)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--source",
        default=os.environ.get("WEAVIATE_COLLECTION", "DOCUMENTS"),
        help="Weaviate source collection (env: WEAVIATE_COLLECTION)",
    )
    ap.add_argument(
        "--dest",
        default=os.environ.get("OPENSEARCH_INDEX", "beyondkm_documents"),
        help=(
            "OpenSearch destination index (env: OPENSEARCH_INDEX). "
            "Default `beyondkm_documents` — kept separate from OpenRAG's "
            "native `documents` (kNN-vector) index to avoid schema conflicts."
        ),
    )
    ap.add_argument("--limit", type=int, default=None, help="max docs to sync")
    ap.add_argument("--batch", type=int, default=200, help="OpenSearch bulk size")
    ap.add_argument("--dry-run", action="store_true", help="fetch only, no indexing")
    args = ap.parse_args()

    print(f"[sync] weaviate  : {WEAVIATE_URL}/{args.source}")
    print(f"[sync] opensearch: {OPENSEARCH_URL}/{args.dest}")
    print(f"[sync] dry_run   : {args.dry_run}  limit: {args.limit}  batch: {args.batch}")

    session: requests.Session | None = None
    if not args.dry_run:
        if not OPENSEARCH_PASSWORD:
            print("[error] OPENSEARCH_PASSWORD env var is required", file=sys.stderr)
            return 2
        session = requests.Session()
        session.auth = HTTPBasicAuth(OPENSEARCH_USER, OPENSEARCH_PASSWORD)
        ensure_os_index(session, args.dest)

    batch: list[dict[str, Any]] = []
    total = 0
    for obj in iter_weaviate(args.source, limit=args.limit):
        _id = obj.pop("_additional", {}).get("id")
        obj["_beyondkm_id"] = _id
        batch.append(obj)
        if len(batch) >= args.batch:
            if session is not None:
                total += bulk_index(session, args.dest, batch)
            else:
                total += len(batch)
            batch = []
            print(f"[sync] progress: {total}")
    if batch:
        if session is not None:
            total += bulk_index(session, args.dest, batch)
        else:
            total += len(batch)

    print(f"[sync] done. synced={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
