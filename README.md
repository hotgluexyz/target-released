# target-released

`target-released` is a Singer target for **ReLeased**, built with the [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK) (`TargetHotglue`, HTTP sinks, SDK authenticators).

This project was generated from the Hotglue target cookiecutter with:

| Option | Value |
|--------|--------|
| `auth_method` | `Bearer Token` |
| `sinks` | `Contacts | record, CreditNotes | record, Invoices | record, Payments | record, CreditNoteAllocation | record` |
| `base_url` | `https://api.re-leased.com/api` |

## Project layout

| Path | Role |
|------|------|
| `target_released/target.py` | `TargetReLeased` — config schema, `SINK_TYPES`, optional `default_sink_class` and `access_token_support` |
| `target_released/client.py` | `ReLeasedSink` base class, `RecordSink` / `BatchSink` stubs, `authenticator` |
| `target_released/sinks.py` | Per-stream sink classes (`FallbackSink` or named `*Sink` classes) |
| `target_released/auth.py` | Present only when generated with **OAuth2** (removed by cookiecutter hook otherwise) |
| `.secrets/config.json` | Sample config matching your `auth_method` |

## Configuration

Settings are defined in `config_jsonschema` on `TargetReLeased` in `target_released/target.py`.
Config matches tap-released: `access_token`, `company_id`, and optional `api_url`.

Inspect the live schema:

```bash
target-released --about
target-released --about --format=markdown
```

Local secrets: edit `.secrets/config.json` (shape matches `auth_method` above).

## Authentication

| `auth_method` | Implementation |
|---------------|----------------|
| **OAuth2** | `ReLeasedAuthenticator` in `auth.py`; `TargetReLeased.access_token_support()` returns the authenticator class and token URL (replace the TODO endpoint in `target.py`). |
| **Bearer Token** | `ReLeasedAuthenticator` in `client.py` (`access_token`) |
| **Basic Auth** | `BasicAuthenticator` with `username` / `password` |
| **API Key** | `ApiAuthenticator` with configurable header name/prefix |

HTTP calls use `base_url` on `ReLeasedSink` (`https://api.re-leased.com/api` unless you change it in `client.py`).

## Sinks

Sinks are declared at scaffold time as `StreamName | record` or `StreamName | batch`, comma-separated.

Generated with: `Contacts | record, CreditNotes | record, Invoices | record, Payments | record, CreditNoteAllocation | record`
**Named sinks:** `sinks.py` defines one class per stream; `target.py` registers them in `SINK_TYPES`. Implement `endpoint` (and request logic) per sink in `sinks.py` / overrides on `client.py` bases.

Shared HTTP behavior lives in `client.py` (`ReLeasedRecordSink` / `ReLeasedBatchSink` as generated).

## Usage

```bash
target-released --version
target-released --help
tap-smoke-test | target-released --config /path/to/config.json
```

## Developer setup

Prerequisites: Python 3.10+, [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run target-released --help
tox -e lint    # ruff check + format
```

For implementation notes and conventions, see `AGENTS.md` (or `CLAUDE.md` if that was selected at generation).

## References

- [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK)
