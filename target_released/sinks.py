"""ReLeased target sinks. Records are scoped to a Company (``company_id``)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from hotglue_singer_sdk.exceptions import FatalAPIError

from target_released.client import ReLeasedRecordSink

# SDK relation_fields: ETL sets ContactId / InvoiceId to QBD externalIds.
_CONTACT_RELATION = [{"field": "ContactId", "objectName": "Contacts"}]
_INVOICE_RELATION = [{"field": "InvoiceId", "objectName": "Invoices"}]


class ContactsSink(ReLeasedRecordSink):
    """Contacts sink — POST/PUT ``/contacts`` under the configured Company.

    Warm-start: SDK ``get_record_id`` sets ``id`` from
    ``Contacts_{flow}.snapshot.csv`` (InputId = QBD ListID). Cold-start /
    name collisions: match existing company contacts by DisplayName via GET.
    Enable flow ``save_ids_snapshots`` so ListID→Id persists across jobs.
    """

    name = "Contacts"
    endpoint = "/contacts"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize sink and an unloaded company contact name→Id cache."""
        super().__init__(*args, **kwargs)
        self._contacts_by_name: dict[str, str] | None = None
        self._exported_contacts_by_name: dict[str, str] = {}

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Map SDK ``id`` → ``GlobalIdentifier``; inject CompanyId on create."""
        payload = self._apply_sdk_id(record, id_key="GlobalIdentifier")
        return self._with_company_id(payload, id_key="GlobalIdentifier")

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Create or update a contact; match existing rows by DisplayName when needed."""
        state_updates: dict = {}
        payload = dict(record)

        if not payload.get("GlobalIdentifier"):
            existing_id = self._contact_id_for_name(payload.get("DisplayName"))
            if existing_id:
                payload["GlobalIdentifier"] = existing_id
                payload.pop("CompanyId", None)

        if payload.get("GlobalIdentifier"):
            self.request_api("PUT", endpoint=self.endpoint, request_data=payload)
            contact_id = payload["GlobalIdentifier"]
            self._remember_exported_contact(payload, contact_id)
            return contact_id, True, {"is_updated": True}

        try:
            response = self.request_api("POST", endpoint=self.endpoint, request_data=payload)
        except FatalAPIError as exc:
            if "already exists" not in str(exc):
                raise
            existing_id = self._contact_id_for_name(payload.get("DisplayName"), refresh=True)
            if not existing_id:
                raise
            payload["GlobalIdentifier"] = existing_id
            payload.pop("CompanyId", None)
            self.request_api("PUT", endpoint=self.endpoint, request_data=payload)
            self._remember_exported_contact(payload, existing_id)
            return existing_id, True, {"is_updated": True}

        record_id = response.json()["Data"]["Id"]
        self._remember_exported_contact(payload, record_id)
        return record_id, True, state_updates

    def _remember_exported_contact(self, payload: dict, contact_id: str) -> None:
        """Keep name→Id in memory for same-job invoice ContactId fallbacks."""
        names = {
            payload.get("DisplayName"),
            payload.get("CorrespondenceName"),
        }
        entries = {name: contact_id for name in names if name}
        if not entries:
            return
        if self._contacts_by_name is None:
            self._exported_contacts_by_name.update(entries)
        else:
            self._contacts_by_name.update(entries)

    def _contact_id_for_name(
        self, display_name: str | None, *, refresh: bool = False
    ) -> str | None:
        """Resolve a ReLeased contact ``Id`` from DisplayName via GET /contacts."""
        if not display_name:
            return None
        if refresh or self._contacts_by_name is None:
            loaded = self._load_contacts_by_name()
            loaded.update(self._exported_contacts_by_name)
            self._exported_contacts_by_name.clear()
            self._contacts_by_name = loaded
        return self._contacts_by_name.get(display_name)

    def _load_contacts_by_name(self) -> dict[str, str]:
        """Page GET ``/contacts`` for this Company into a name→Id map."""
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
    """Invoices sink — POST ``/invoices`` only (create; no updates).

    Covers both ACCREC (QBD invoices) and ACCPAY (QBD bills). ``externalId`` is
    QBD TxnID; SDK snapshots map to ``Id`` so re-exports count as existing.
    ``ContactId`` arrives as contact ListID and is resolved via ``relation_fields``.
    """

    name = "Invoices"
    endpoint = "/invoices"
    relation_fields = _CONTACT_RELATION

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Map SDK ``id`` → ``Id`` and inject CompanyId on create."""
        payload = self._apply_sdk_id(record)
        return self._with_company_id(payload)

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Skip non-positive totals and already-exported rows; POST create only."""
        if record.get("Id"):
            self.logger.info(
                "Skipping invoice already exported Id=%s Reference=%s Type=%s",
                record.get("Id"),
                record.get("Reference"),
                record.get("Type"),
            )
            return record["Id"], True, {"existing": True}

        total = self._invoice_total(record)
        if total <= 0:
            self.logger.info(
                "Skipping invoice with non-positive total=%s Reference=%s Type=%s",
                total,
                record.get("Reference"),
                record.get("Type"),
            )
            return record.get("Reference"), True, {"skipped": True}

        response = self.request_api("POST", endpoint=self.endpoint, request_data=record)
        return response.json()["Data"]["Id"], True, {}

    @staticmethod
    def _invoice_total(record: dict) -> float:
        """Compute the invoice total for the non-positive skip check."""
        for key in ("Total", "SubTotal"):
            if record.get(key) is not None:
                return float(record[key])

        total = 0.0
        for line in record.get("LineItems") or []:
            quantity = float(line.get("Quantity") or 0)
            unit_amount = float(line.get("UnitAmount") or 0)
            total += quantity * unit_amount
        return total


class CreditNotesSink(ReLeasedRecordSink):
    """CreditNotes sink — POST ``/credit-notes`` only (create; no updates)."""

    name = "CreditNotes"
    endpoint = "/credit-notes"
    relation_fields = _CONTACT_RELATION

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Map SDK ``id`` → ``Id`` and inject CompanyId on create."""
        payload = self._apply_sdk_id(record)
        return self._with_company_id(payload)

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Skip already-exported rows; POST create only."""
        if record.get("Id"):
            self.logger.info(
                "Skipping credit note already exported Id=%s Reference=%s",
                record.get("Id"),
                record.get("Reference"),
            )
            return record["Id"], True, {"existing": True}

        response = self.request_api("POST", endpoint=self.endpoint, request_data=record)
        return response.json()["Data"]["Id"], True, {}


class PaymentsSink(ReLeasedRecordSink):
    """Payments sink — POST ``/payments`` only (create; no updates).

    ``InvoiceId`` arrives as the related QBD bill/invoice TxnID and is resolved
    via ``relation_fields`` to a Re-Leased invoice uuid. ``externalId`` is
    ``{paymentTxnId}:{appliedTxnId}`` so multi-application payments snapshot
    cleanly. CreatePayment does not take CompanyId (invoice scopes the company).
    """

    name = "Payments"
    endpoint = "/payments"
    relation_fields = _INVOICE_RELATION

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Map SDK ``id`` → ``Id`` for already-exported payments."""
        return self._apply_sdk_id(record)

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Skip already-exported rows; POST create only."""
        if record.get("Id"):
            self.logger.info(
                "Skipping payment already exported Id=%s InvoiceId=%s Reference=%s",
                record.get("Id"),
                record.get("InvoiceId"),
                record.get("Reference"),
            )
            return record["Id"], True, {"existing": True}

        response = self.request_api("POST", endpoint=self.endpoint, request_data=record)
        body = response.json()
        # Live API wraps create in ``Data``; swagger examples sometimes omit it.
        if isinstance(body.get("Data"), dict) and body["Data"].get("Id"):
            return body["Data"]["Id"], True, {}
        return body["Id"], True, {}


class CreditNoteAllocationSink(ReLeasedRecordSink):
    """CreditNoteAllocation sink — POST ``/credit-notes/{id}/allocations``."""

    name = "CreditNoteAllocation"
    endpoint = "/credit-notes/"

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """POST allocations for a credit note."""
        credit_note_id = record["CreditNoteId"]
        payload = {"Allocations": record["Allocations"]}
        endpoint = f"{self.endpoint}{credit_note_id}/allocations"
        response = self.request_api("POST", endpoint=endpoint, request_data=payload)
        record_id = response.json().get("Data", {}).get("Id") or credit_note_id
        return record_id, True, {}
