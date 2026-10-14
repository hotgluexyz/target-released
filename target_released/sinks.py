"""ReLeased target sink class, which handles writing streams."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from hotglue_singer_sdk.exceptions import FatalAPIError

from target_released.client import ReLeasedRecordSink


class ContactsSink(ReLeasedRecordSink):
    """Contacts sink — POST/PUT ``/contacts``."""

    name = "Contacts"
    endpoint = "/contacts"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._contacts_by_name: dict[str, str] | None = None

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Pass through native Re-Leased fields; inject CompanyId on create."""
        payload = dict(record)
        if not payload.get("GlobalIdentifier"):
            payload["CompanyId"] = self.config["company_id"]
        return payload

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        payload = dict(record)

        if not payload.get("GlobalIdentifier"):
            existing_id = self._contact_id_for_name(payload.get("DisplayName"))
            if existing_id:
                payload["GlobalIdentifier"] = existing_id
                payload.pop("CompanyId", None)

        if payload.get("GlobalIdentifier"):
            self.request_api("PUT", endpoint=self.endpoint, request_data=payload)
            return payload["GlobalIdentifier"], True, {"is_updated": True}

        try:
            response = self.request_api(
                "POST", endpoint=self.endpoint, request_data=payload
            )
        except FatalAPIError as exc:
            if "already exists" not in str(exc):
                raise
            existing_id = self._contact_id_for_name(
                payload.get("DisplayName"), refresh=True
            )
            if not existing_id:
                raise
            payload["GlobalIdentifier"] = existing_id
            payload.pop("CompanyId", None)
            self.request_api("PUT", endpoint=self.endpoint, request_data=payload)
            return existing_id, True, {"is_updated": True}

        record_id = response.json()["Data"]["Id"]
        display_name = payload.get("DisplayName")
        if display_name:
            self._contacts_by_name_map()[display_name] = record_id
        return record_id, True, state_updates

    def _contacts_by_name_map(self) -> dict[str, str]:
        if self._contacts_by_name is None:
            self._contacts_by_name = self._load_contacts_by_name()
        return self._contacts_by_name

    def _contact_id_for_name(
        self, display_name: str | None, *, refresh: bool = False
    ) -> str | None:
        if not display_name:
            return None
        if refresh:
            self._contacts_by_name = self._load_contacts_by_name()
        return self._contacts_by_name_map().get(display_name)

    def _load_contacts_by_name(self) -> dict[str, str]:
        """Page GET /contacts and index Name/DisplayName/CorrespondenceName → Id."""
        by_name: dict[str, str] = {}
        params: dict[str, str] = {"companyId": self.config["company_id"]}

        while True:
            response = self.request_api("GET", endpoint=self.endpoint, params=dict(params))
            body = response.json()
            for contact in body.get("Data") or []:
                contact_id = contact["Id"]
                for key in ("Name", "DisplayName", "CorrespondenceName"):
                    name = contact.get(key)
                    if name:
                        by_name[str(name)] = contact_id

            next_url = (body.get("MetaData") or {}).get("NextPageUrl")
            if not next_url:
                break
            query = parse_qs(urlparse(next_url).query)
            from_id = (query.get("fromId") or [None])[0]
            if not from_id:
                break
            params = {
                "companyId": self.config["company_id"],
                "fromId": from_id,
            }

        return by_name


class InvoicesSink(ReLeasedRecordSink):
    """Invoices sink — POST/PUT ``/invoices``."""

    name = "Invoices"
    endpoint = "/invoices"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        payload = dict(record)
        if not payload.get("Id"):
            payload["CompanyId"] = self.config["company_id"]
        return payload

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        method = "POST"
        if record.get("Id"):
            method = "PUT"
            state_updates["is_updated"] = True

        response = self.request_api(method, endpoint=self.endpoint, request_data=record)

        if method == "PUT":
            record_id = record["Id"]
        else:
            record_id = response.json()["Data"]["Id"]

        return record_id, True, state_updates


class CreditNotesSink(ReLeasedRecordSink):
    """CreditNotes sink — POST/PUT ``/credit-notes``."""

    name = "CreditNotes"
    endpoint = "/credit-notes"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        payload = dict(record)
        if not payload.get("Id"):
            payload["CompanyId"] = self.config["company_id"]
        return payload

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        method = "POST"
        if record.get("Id"):
            method = "PUT"
            state_updates["is_updated"] = True

        response = self.request_api(method, endpoint=self.endpoint, request_data=record)

        if method == "PUT":
            record_id = record["Id"]
        else:
            record_id = response.json()["Data"]["Id"]

        return record_id, True, state_updates


class PaymentsSink(ReLeasedRecordSink):
    """Payments sink — POST ``/payments`` (create only)."""

    name = "Payments"
    endpoint = "/payments"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        return dict(record)

    def upsert_record(self, record: dict, context: dict):
        response = self.request_api("POST", endpoint=self.endpoint, request_data=record)
        record_id = response.json()["Data"]["Id"]
        return record_id, True, {}


class CreditNoteAllocationSink(ReLeasedRecordSink):
    """CreditNoteAllocation sink — POST ``/credit-notes/{id}/allocations``."""

    name = "CreditNoteAllocation"
    endpoint = "/credit-notes/"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        return dict(record)

    def upsert_record(self, record: dict, context: dict):
        credit_note_id = record["CreditNoteId"]
        payload = {"Allocations": record["Allocations"]}
        endpoint = f"{self.endpoint}{credit_note_id}/allocations"
        response = self.request_api("POST", endpoint=endpoint, request_data=payload)
        record_id = response.json().get("Data", {}).get("Id") or credit_note_id
        return record_id, True, {}
