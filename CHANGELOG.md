# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- BeyondKM connector (`src/connectors/beyondkm.py`) for cross-service data sync
- Integration scripts: `sync_beyondkm_to_openrag.py`, `setup_and_sync.sh`, `bootstrap_integration.sh`
- Utility scripts: `docling_run.sh`, `fix_embedding_component.py`, `fix_flow_table_type.py`, `generate_secrets.sh`, `reimport_flows.sh`
- `INTEGRATION.md` — BeyondKM integration documentation
- `docker-compose.override.yml` for local development overrides
- FastMCP streamable HTTP server integration
- Smoke tests for onboarding flow

### Changed
- Updated Langflow to v1.9
- Updated SDK release to 0.3.0
- Updated OpenRAG agent, ingestion, nudges, and URL MCP flows
- Changed docker namespace for OpenSearch

### Fixed
- Store raw JWT in auth cookie (strip 'Bearer ' prefix)
- Re-add litellm; fix ingestion without Langflow for all providers
- Fix search functionality for all providers
- Fix httpx resolution error in MCP
- Fix tooltip visibility

---

## History

| Commit | Type | Description |
|--------|------|-------------|
| `a69f55b` | feat | Add BeyondKM integration and sync scripts |
| `3fc5ee7` | chore | Changed docker namespace for opensearch |
| `3703fc2` | fix | Fix tooltip visibility |
| `b901448` | feat | Updated langflow to 1.9 |
| `796cc2f` | feat | Integrate FastMCP streamable HTTP server |
| `46065c8` | chore | SDK release 0.3.0 |
| `aca5076` | fix | Store raw JWT in auth cookie |
| `a2fdfba` | feat | Add smoke tests for onboarding |
| `f7b6db5` | fix | Re-add litellm, fix ingestion and search |
| `7305e10` | fix | Fix httpx resolution error in mcp |
