# aimlab_openrag ↔ cad_beyondkm integration

Strategy **(A) Coexist**: both stacks run side-by-side on a shared Docker
network. OpenRAG keeps its own OpenSearch for native operations; BeyondKM keeps
Weaviate/Postgres/Memgraph untouched. A single optional bridge (a custom
Langflow component) lets OpenRAG flows read from BeyondKM's Weaviate when
relevant — no data migration, no upstream patch.

## Network map

```
┌───────────────────── beyondkm_shared (external) ──────────────────────┐
│                                                                       │
│   cad_beyondkm stack                aimlab_openrag stack              │
│   ┌───────────────┐                 ┌───────────────────┐             │
│   │ postgres:5432 │◄────────────────┤ openrag-backend   │             │
│   │ weaviate:8080 │◄────────────────┤ langflow:7860     │             │
│   │ memgraph:7687 │◄────────────────┤                   │             │
│   │ redis:6379    │                 └───────────────────┘             │
│   │ azurite:10000 │                                                   │
│   │ api:8000      │                                                   │
│   └───────────────┘                                                   │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
              │                                   │
              │ default network                   │ default network
              ▼                                   ▼
   cad_beyondkm_app:3003              opensearch:9200, dashboards:5601,
                                      openrag-frontend:3000
```

## One-time setup

```bash
# 1. Ensure BeyondKM stack is up (postgres is optional — uses Azure PG externally)
cd /Users/kanit/Developer/repo/work/projects/cad_beyondkm/keen-dhawan-2d26e5
docker compose up -d

# 2. Create shared network + attach BeyondKM containers as aliases
cd /Users/kanit/Developer/repo/work/projects/aimlab_openrag
./scripts/bootstrap_integration.sh

# 3. Fill REPLACE_ME_* secrets in .env with strong dev values
./scripts/generate_secrets.sh

# 4. Start OpenRAG
docker compose up -d

# 5. (Optional) Sync BeyondKM's Weaviate → OpenRAG OpenSearch
OPENSEARCH_PASSWORD="$(grep ^OPENSEARCH_PASSWORD= .env | cut -d= -f2)" \
  uv run --with requests --no-project \
    python scripts/sync_beyondkm_to_openrag.py --limit 500
```

## Data sync (Weaviate → OpenSearch)

`scripts/sync_beyondkm_to_openrag.py` is a one-shot mirror. It pages through
BeyondKM's `DOCUMENTS` collection via GraphQL and bulk-indexes into OpenRAG's
`documents` index. Flags:

| flag        | default | purpose                                    |
|-------------|---------|--------------------------------------------|
| `--limit N` | all     | cap fetched docs (test with `--limit 50`)  |
| `--batch N` | 200     | OpenSearch `_bulk` request size            |
| `--dry-run` | off     | fetch only, no OpenSearch writes           |

Env overrides: `WEAVIATE_URL`, `WEAVIATE_COLLECTION`, `OPENSEARCH_URL`,
`OPENSEARCH_INDEX`, `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD`.

Re-running is safe — documents are upserted by `_beyondkm_id` (Weaviate UUID).

## How each connection is wired

| OpenRAG need            | Target                        | Mechanism                                        |
|-------------------------|-------------------------------|--------------------------------------------------|
| Langflow metadata DB    | `postgres` (shared)           | `LANGFLOW_DATABASE_URL` → `langflow` db          |
| Vector store (native)   | `opensearch` (in this stack)  | unchanged — OpenRAG's own OpenSearch             |
| Bridge to BeyondKM vecs | `weaviate:8080` (shared)      | Custom Langflow component — see TODO(human) below|
| Bridge to BeyondKM SQL  | `postgres:5432/beyondkm`      | Read-only queries from flows                     |
| Bridge to BeyondKM KG   | `memgraph:7687`               | Bolt client in a flow                            |

Env vars `BEYONDKM_WEAVIATE_URL`, `BEYONDKM_POSTGRES_URL`, `BEYONDKM_MEMGRAPH_URL`
are injected into `openrag-backend` and `langflow` by
`docker-compose.override.yml` so any bridge code can pick them up without
hardcoding.

## Why OpenSearch is kept (not swapped for Weaviate)

- gist-openrag's flows, ingest pipeline, and security bootstrap assume
  OpenSearch. Replacing it would fork the upstream project.
- Coexistence preserves **both** data surfaces independently, matching the
  polyglot-store pattern already used in BeyondKM (Memgraph for KG,
  Weaviate for vectors, Postgres as source of truth).

## Bridge component — `src/connectors/beyondkm.py`

A read-side connector exposing `retrieve_from_beyondkm()`. Scoring and
weighting mirror OpenRAG's native OpenSearch recipe
(`src/services/search_service.py`).

### API

```python
retrieve_from_beyondkm(
    query,
    *,
    method="hybrid",          # "hybrid" | "near_text" | "bm25" | "near_vector"
    collection=None,          # env: BEYONDKM_COLLECTION (no in-function default)
    workspace=None,           # filter: workspace == <uuid>
    top_k=5,
    alpha=0.7,                # matches OpenRAG's 70% semantic / 30% keyword
    min_score=None,           # OpenRAG-style score floor, drops weak hits
    vector=None,              # caller-supplied embedding
) -> list[BridgeHit]
```

### Method matrix

| method        | needs vector? | notes                                              |
|---------------|---------------|----------------------------------------------------|
| `hybrid`      | optional      | default; **degrades to `bm25` when no vector** (BeyondKM's Weaviate has no server-side vectorizer) |
| `near_text`   | yes†          | requires vector (same constraint)                  |
| `bm25`        | no            | keyword-only, safe anywhere                        |
| `near_vector` | required      | pure semantic with your own embedding              |

† In a stock Weaviate with a vectorizer module, `near_text` works without a
vector. BeyondKM's Weaviate is deliberately vectorizer-free (embeddings are
generated upstream by BeyondKM's API), so a caller-supplied vector is needed.

### Scoring (OpenRAG-native recommended)

- `hybrid` / `bm25` → Weaviate's normalized score in `BridgeHit.score`
- `near_*` → `1.0 - distance` (so "higher is better" for all methods)
- Pass `min_score` to emulate OpenRAG's `search_body["min_score"]` threshold
- Results sorted descending by score — ready for RRF/fusion with OpenSearch hits

## OpenSearch index layout

Two separate indices, kept apart to avoid kNN-schema conflicts:

| Index                 | Owner      | Schema                                     | Contents                            |
|-----------------------|------------|--------------------------------------------|-------------------------------------|
| `documents`           | OpenRAG    | `index.knn: true`, `knn_vector` fields     | Docs uploaded via OpenRAG UI        |
| `beyondkm_documents`  | this repo  | plain text/keyword                         | Mirror of BeyondKM Weaviate docs    |

Why separate: OpenRAG's search code dereferences `KNNMethodConfigContext` on
its native index; placing plain-text docs in `documents` would NPE with:

```
null_pointer_exception: Cannot invoke "KNNMethodConfigContext.getVectorDataType()"
because "knnMethodConfigContext" is null
```

The sync script defaults to `--dest beyondkm_documents`. If you changed
`OPENSEARCH_INDEX_NAME` in `.env`, the script honors the override via the
`OPENSEARCH_INDEX` env var.

## Known issue: bundled Langflow flows vs image version skew

The repo's `flows/*.json` were built against OpenRAG 0.3.x (Langflow "lfx" API),
but the only published Docker tag is `0.4.1`. The 0.4.x lfx runtime removed
several symbols the bundled flows still reference:

| Removed / renamed symbol                          | Status                    |
|---------------------------------------------------|---------------------------|
| `lfx.schema.dataframe.Table`                      | Renamed → `DataFrame`     |
| `lfx.base.models.unified_models.get_embeddings`   | Removed entirely          |
| `lfx.base.models.unified_models.handle_model_input_update` | Replaced by `update_model_options_in_build_config` |

`scripts/fix_flow_table_type.py` handles the `Table` → `DataFrame` rewrite
(string literals, edge handles, and Python source identifiers), but
`get_embeddings` is an API removal, not a rename — a string-level fix can't
do it. It requires an upstream flow rebuild (waiting on langflowai).

**Mitigation in effect**: `.env` has `DISABLE_INGEST_WITH_LANGFLOW=true`.
This routes file uploads through OpenRAG's native docling-based processor
(which works once `docling-serve` is running via `scripts/docling_run.sh`).
Two things remain broken until an upstream image bundles 0.4-compatible flows:

- **URL ingestion** (uses `openrag_url_mcp.json`)
- **Chat-flow features backed by Langflow** (`openrag_agent.json`)

To watch for the upstream fix: `docker pull langflowai/openrag-langflow:latest`
periodically and bump `OPENRAG_VERSION` in `.env`.

## Ports reference

| Service                | Host port | Stack       |
|------------------------|-----------|-------------|
| cad_beyondkm_app       | 3003      | beyondkm    |
| cad_beyondkm_api       | 8000      | beyondkm    |
| postgres               | 5432      | beyondkm    |
| weaviate (HTTP)        | 8085      | beyondkm    |
| weaviate (gRPC)        | 50051     | beyondkm    |
| memgraph (Bolt)        | 7687      | beyondkm    |
| redis                  | 6379      | beyondkm    |
| azurite (blob)         | 10000     | beyondkm    |
| openrag-frontend       | 3000      | openrag     |
| langflow               | 7860      | openrag     |
| opensearch             | 9200      | openrag     |
| opensearch dashboards  | 5601      | openrag     |
