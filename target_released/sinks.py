"""ReLeased target sink class, which handles writing streams."""

from __future__ import annotations

from target_released.client import ReLeasedRecordSink


class ContactsSink(ReLeasedRecordSink):
    """Contacts sink — POST/PUT ``/contact``."""

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


class CreditNotesSink(ReLeasedRecordSink):
    """CreditNotes sink implementation."""

    name = "CreditNotes"
    endpoint = "/endpoint"  # TODO: Add the actual endpoint for the sink


class InvoicesSink(ReLeasedRecordSink):
    """Invoices sink implementation."""

    name = "Invoices"
    endpoint = "/endpoint"  # TODO: Add the actual endpoint for the sink


class PaymentsSink(ReLeasedRecordSink):
    """Payments sink implementation."""

    name = "Payments"
    endpoint = "/endpoint"  # TODO: Add the actual endpoint for the sink


class CreditNoteAllocationSink(ReLeasedRecordSink):
    """CreditNoteAllocation sink implementation."""

    name = "CreditNoteAllocation"
    endpoint = "/endpoint"  # TODO: Add the actual endpoint for the sink
