"""Bridge connector: read from the cad_beyondkm Weaviate stack.

Reachable on the shared Docker network `beyondkm_shared`. Connection URL is
injected by docker-compose.override.yml via `BEYONDKM_WEAVIATE_URL`.

OpenRAG keeps its own OpenSearch for native operations; this module only
exposes a read-side query path into BeyondKM's Weaviate for flows that want
to blend BeyondKM knowledge into an OpenRAG response.

Scoring and weighting mirror OpenRAG's native OpenSearch hybrid recipe
(70% semantic / 30% keyword with a caller-supplied `min_score` threshold —
see `search_service.py`). Weaviate's hybrid `score` is returned verbatim so
it can be rank-fused with OpenSearch hits upstream.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import weaviate
from weaviate.classes.query import Filter, MetadataQuery

RetrievalMethod = Literal["hybrid", "near_text", "bm25", "near_vector"]

DEFAULT_METHOD: RetrievalMethod = "hybrid"
DEFAULT_ALPHA = 0.7


def _env(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val else default


def _collection_default() -> str:
    return _env("BEYONDKM_COLLECTION", "DOCUMENTS")


def _weaviate_url() -> str:
    return _env("BEYONDKM_WEAVIATE_URL", "http://weaviate:8080")


def _grpc_port() -> int:
    return int(_env("BEYONDKM_WEAVIATE_GRPC_PORT", "50051"))


@dataclass(frozen=True)
class BridgeHit:
    """One retrieved item from BeyondKM, normalized for OpenRAG consumption."""

    id: str
    content: str
    score: float
    source: str
    filename: str
    workspace: str
    metadata: dict[str, object]


def _client() -> weaviate.WeaviateClient:
    parsed = urlparse(_weaviate_url())
    host = parsed.hostname or "weaviate"
    http_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return weaviate.connect_to_custom(
        http_host=host,
        http_port=http_port,
        http_secure=parsed.scheme == "https",
        grpc_host=host,
        grpc_port=_grpc_port(),
        grpc_secure=False,
    )


def _build_filter(workspace: str | None) -> Filter | None:
    if not workspace:
        return None
    return Filter.by_property("workspace").equal(workspace)


def _run_query(
    col: object,
    *,
    query: str,
    method: RetrievalMethod,
    top_k: int,
    alpha: float,
    vector: list[float] | None,
    where: Filter | None,
) -> list[object]:
    md = MetadataQuery(score=True, distance=True)
    # BeyondKM's collection has no server-side vectorizer (DEFAULT_VECTORIZER_MODULE=none).
    # Hybrid/near_text require a caller-supplied vector. Without one, degrade to bm25.
    if method == "hybrid" and vector is None:
        method = "bm25"
    elif method == "near_text" and vector is None:
        method = "bm25"

    if method == "hybrid":
        resp = col.query.hybrid(  # type: ignore[attr-defined]
            query=query,
            vector=vector,
            alpha=alpha,
            limit=top_k,
            filters=where,
            return_metadata=md,
        )
    elif method == "near_text":
        resp = col.query.near_text(  # type: ignore[attr-defined]
            query=query, limit=top_k, filters=where, return_metadata=md
        )
    elif method == "bm25":
        resp = col.query.bm25(  # type: ignore[attr-defined]
            query=query, limit=top_k, filters=where, return_metadata=md
        )
    elif method == "near_vector":
        if vector is None:
            raise ValueError("near_vector requires an embedding via `vector=`")
        resp = col.query.near_vector(  # type: ignore[attr-defined]
            near_vector=vector, limit=top_k, filters=where, return_metadata=md
        )
    else:  # pragma: no cover — Literal guard
        raise ValueError(f"unknown method: {method}")
    return list(resp.objects)


def _to_hit(obj: object) -> BridgeHit:
    props: dict[str, object] = getattr(obj, "properties", {}) or {}
    meta = getattr(obj, "metadata", None)
    # Weaviate hybrid & bm25 populate `score`; near_* populate `distance`.
    raw_score = getattr(meta, "score", None)
    distance = getattr(meta, "distance", None)
    score = float(raw_score) if raw_score is not None else (
        1.0 - float(distance) if distance is not None else 0.0
    )
    return BridgeHit(
        id=str(getattr(obj, "uuid", "")),
        content=str(props.get("content", "")),
        score=score,
        source=str(props.get("source", "")),
        filename=str(props.get("filename", "")),
        workspace=str(props.get("workspace", "")),
        metadata={
            k: v
            for k, v in props.items()
            if k not in ("content", "source", "filename", "workspace")
        },
    )


def retrieve_from_beyondkm(
    query: str,
    *,
    method: RetrievalMethod = DEFAULT_METHOD,
    collection: str | None = None,
    workspace: str | None = None,
    top_k: int = 5,
    alpha: float = DEFAULT_ALPHA,
    min_score: float | None = None,
    vector: list[float] | None = None,
) -> list[BridgeHit]:
    """Query BeyondKM's Weaviate and return ranked, normalized hits.

    Args:
        query: Natural-language query.
        method: Retrieval method. `"hybrid"` (default) matches OpenRAG's
            native recipe; other options exposed for experimentation.
        collection: Weaviate collection name. Defaults to env
            `BEYONDKM_COLLECTION` (fallback `"DOCUMENTS"`). Never hardcoded
            inside the function body.
        workspace: Optional workspace filter; applied as an equality filter
            on the `workspace` property.
        top_k: Max hits returned.
        alpha: Weaviate hybrid alpha. `0.7` matches OpenRAG's native
            70% semantic / 30% keyword weighting. `1.0`=pure vector,
            `0.0`=pure BM25.
        min_score: OpenRAG-style score floor. Hits below are dropped. `None`
            disables filtering.
        vector: Pre-computed embedding; required only when `method="near_vector"`.

    Returns:
        List of BridgeHit sorted by descending `score` (score is Weaviate's
        normalized hybrid/BM25 score, or `1 - distance` for near_* methods).
    """
    col_name = collection or _collection_default()
    client = _client()
    try:
        col = client.collections.get(col_name)
        objs = _run_query(
            col,
            query=query,
            method=method,
            top_k=top_k,
            alpha=alpha,
            vector=vector,
            where=_build_filter(workspace),
        )
    finally:
        client.close()

    hits = [_to_hit(o) for o in objs]
    if min_score is not None:
        hits = [h for h in hits if h.score >= min_score]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
