"""HTTP client and sink base classes for ReLeased."""

from __future__ import annotations

import uuid
from typing import Any

from hotglue_singer_sdk.target_sdk.auth import BearerTokenAuthenticator
from hotglue_singer_sdk.target_sdk.client import HotglueBaseSink, HotglueSink


class ReLeasedAuthenticator(BearerTokenAuthenticator):
    """Bearer auth using ``access_token`` (same config key as tap-released)."""

    @property
    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._config['access_token']}"}


class ReLeasedSink(HotglueBaseSink):
    """ReLeased Base target sink class for sinks."""

    @property
    def base_url(self) -> str:
        return self.config.get("api_url", "https://api.re-leased.com/api")

    @property
    def authenticator(self) -> Any:
        return ReLeasedAuthenticator(self._target)

    @property
    def http_headers(self) -> dict:
        return {"Idempotency-Key": str(uuid.uuid4())}


class ReLeasedRecordSink(ReLeasedSink, HotglueSink):
    """ReLeased Record target sink class for record sinks."""

    name = "ReLeasedRecordSink"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        return record

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = {}
        pk_field = self.key_properties[0] if self.key_properties else "id"
        pk = record.get(pk_field)
        method = "POST"
        endpoint = self.endpoint

        if pk:
            method = "PATCH"
            endpoint = f"{endpoint}{pk}"
            state_updates["is_updated"] = True

        response = self.request_api(method, endpoint, request_data=record)
        record_id = pk or response.json().get("id")
        return record_id, True, state_updates
