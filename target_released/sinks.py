"""ReLeased target sink class, which handles writing streams."""

from __future__ import annotations

from target_released.client import ReLeasedRecordSink


class ContactsSink(ReLeasedRecordSink):
    """Contacts sink — POST/PUT ``/contacts``."""

    name = "Contacts"
    endpoint = "/contacts"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Pass through native Re-Leased fields; inject CompanyId on create."""
        payload = dict(record)
        if not payload.get("GlobalIdentifier"):
            payload["CompanyId"] = self.config["company_id"]
        return payload

    def upsert_record(self, record: dict, context: dict):
        state_updates = {}
        method = "POST"
        if record.get("GlobalIdentifier"):
            method = "PUT"
            state_updates["is_updated"] = True

        response = self.request_api(method, endpoint=self.endpoint, request_data=record)

        if method == "PUT":
            record_id = record["GlobalIdentifier"]
        else:
            record_id = response.json()["Data"]["Id"]

        return record_id, True, state_updates


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
