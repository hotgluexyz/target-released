"""HTTP client and sink base classes for ReLeased."""

from __future__ import annotations

import uuid
from typing import Any

import requests
from hotglue_singer_sdk.exceptions import FatalAPIError, RetriableAPIError
from hotglue_singer_sdk.target_sdk.auth import BearerTokenAuthenticator
from hotglue_singer_sdk.target_sdk.client import HotglueBaseSink, HotglueSink


def format_released_api_error(response: requests.Response) -> str:
    """Prefer Re-Leased ``Errors[].Code/Message`` over empty ``Detail`` JSON."""
    text = (response.text or "").strip()
    try:
        body = response.json()
    except ValueError:
        return text or response.reason or f"HTTP {response.status_code}"

    if not isinstance(body, dict):
        return text or response.reason or f"HTTP {response.status_code}"

    parts: list[str] = []
    for err in body.get("Errors") or []:
        if not isinstance(err, dict):
            continue
        code = err.get("Code")
        message = err.get("Message")
        if code and message:
            parts.append(f"{code}: {message}")
        elif message:
            parts.append(str(message))
        elif code:
            parts.append(str(code))
    if parts:
        return "; ".join(parts)

    detail = body.get("Detail")
    title = body.get("Title")
    if detail:
        return f"{title}: {detail}" if title else str(detail)
    if title:
        return str(title)
    return text or response.reason or f"HTTP {response.status_code}"


class ReLeasedAuthenticator(BearerTokenAuthenticator):
    """Bearer auth using ``access_token`` (same config key as tap-released)."""

    @property
    def auth_headers(self) -> dict:
        """Return Authorization headers for the ReLeased API.

        Returns:
            Dict with a Bearer ``Authorization`` header.
        """
        return {"Authorization": f"Bearer {self._config['access_token']}"}


class ReLeasedSink(HotglueBaseSink):
    """ReLeased Base target sink class for sinks."""

    @property
    def base_url(self) -> str:
        """Return the ReLeased API base URL.

        Hardcoded so Hotglue OAuth ``api_url`` (entity auth callback) cannot
        override the destination host.
        """
        return "https://api.re-leased.com/api"

    @property
    def authenticator(self) -> Any:
        """Return the authenticator for outbound requests.

        Returns:
            A ``ReLeasedAuthenticator`` bound to this target.
        """
        return ReLeasedAuthenticator(self._target)

    @property
    def http_headers(self) -> dict:
        """Return extra HTTP headers for each request.

        Returns:
            Dict including a fresh ``Idempotency-Key``.
        """
        return {"Idempotency-Key": str(uuid.uuid4())}

    def validate_response(self, response: requests.Response) -> None:
        """Raise with Re-Leased ``Errors[].Message`` (e.g. overpayment) visible."""
        if response.status_code in [429] or 500 <= response.status_code < 600:
            raise RetriableAPIError(self.response_error_message(response), response)
        if 400 <= response.status_code < 500:
            raise FatalAPIError(format_released_api_error(response))


class ReLeasedRecordSink(ReLeasedSink, HotglueSink):
    """Record sink base. All writes are scoped to config ``company_id`` (a Company)."""

    name = "ReLeasedRecordSink"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Pass the record through unchanged.

        Args:
            record: Singer record payload for this stream.
            context: SDK batch/context dict (unused here).

        Returns:
            The same ``record`` dict.
        """
        return record

    def _apply_sdk_id(self, record: dict, *, id_key: str = "Id") -> dict:
        """Copy SDK ``id`` from id-snapshot lookup onto the ReLeased id field.

        ``TargetHotglue.get_record_id`` sets lowercase ``id`` from
        ``{stream}_{flow}.snapshot.csv``. ReLeased APIs use ``Id`` or
        ``GlobalIdentifier``.
        """
        payload = dict(record)
        sdk_id = payload.pop("id", None)
        if sdk_id and not payload.get(id_key):
            payload[id_key] = str(sdk_id)
        return payload

    def _with_company_id(self, record: dict, *, id_key: str = "Id") -> dict:
        """Copy record and set CompanyId on create when ``id_key`` is absent.

        Args:
            record: Sink record (contact, invoice, credit note, etc.).
            id_key: Field that marks an existing ReLeased row. Default ``Id``;
                contacts use ``GlobalIdentifier`` (the contact's API ``Id``).

        Returns:
            A shallow copy of ``record``, with ``CompanyId`` set from config when
            creating (no value at ``id_key``).
        """
        payload = dict(record)
        if not payload.get(id_key):
            payload["CompanyId"] = self.config["company_id"]
        return payload

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """POST to create, or PUT when ``Id`` is present (ReLeased API convention).

        Args:
            record: Already-preprocessed payload for the destination API.
            context: SDK batch/context dict (unused here).

        Returns:
            Tuple of ``(record_id, success, state_updates)`` where ``record_id``
            is the ReLeased ``Id``, ``success`` is always True on HTTP success,
            and ``state_updates`` may include ``is_updated``.
        """
        state_updates: dict = {}
        method = "POST"
        if record.get("Id"):
            method = "PUT"
            state_updates["is_updated"] = True

        response = self.request_api(method, endpoint=self.endpoint, request_data=record)
        record_id = record["Id"] if method == "PUT" else response.json()["Data"]["Id"]
        return record_id, True, state_updates
