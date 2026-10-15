"""ReLeased target sinks. Records are scoped to a Company (``company_id``)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from hotglue_singer_sdk.exceptions import FatalAPIError

from target_released.client import ReLeasedRecordSink


class ContactsSink(ReLeasedRecordSink):
    """Contacts sink — POST/PUT ``/contacts`` under the configured Company.

    On write, ReLeased identifies an existing contact with ``GlobalIdentifier``,
    which is the contact's API ``Id`` (from GET ``/contacts`` ``Data[].Id``).
    """

    name = "Contacts"
    endpoint = "/contacts"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize sink and an unloaded company contact name→Id cache.

        Args:
            *args: Positional args forwarded to the SDK sink base.
            **kwargs: Keyword args forwarded to the SDK sink base.
        """
        super().__init__(*args, **kwargs)
        self._contacts_by_name: dict[str, str] | None = None

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Inject CompanyId on create when GlobalIdentifier is absent.

        Args:
            record: Contact payload. ``GlobalIdentifier``, when set, is the
                ReLeased contact ``Id`` used for PUT updates.
            context: SDK batch/context dict (unused here).

        Returns:
            Payload with ``CompanyId`` set when creating a new contact.
        """
        return self._with_company_id(record, id_key="GlobalIdentifier")

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Create or update a contact; match existing rows by DisplayName when needed.

        Args:
            record: Contact payload after ``preprocess_record``. May include
                ``GlobalIdentifier`` (ReLeased contact ``Id``) and ``DisplayName``.
            context: SDK batch/context dict (unused here).

        Returns:
            Tuple of ``(contact_id, success, state_updates)``.
        """
        state_updates: dict = {}
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
            return existing_id, True, {"is_updated": True}

        record_id = response.json()["Data"]["Id"]
        display_name = payload.get("DisplayName")
        if display_name:
            if self._contacts_by_name is None:
                self._contacts_by_name = {}
            self._contacts_by_name[display_name] = record_id
        return record_id, True, state_updates

    def _contact_id_for_name(
        self, display_name: str | None, *, refresh: bool = False
    ) -> str | None:
        """Resolve a ReLeased contact ``Id`` from a contact name field.

        Uses an in-memory cache of company contacts (``_contacts_by_name``).
        ``_load_contacts_by_name`` runs once on first miss, then only again when
        ``refresh=True`` (e.g. after an ``already exists`` POST error)—not once
        per Singer record.

        Args:
            display_name: Contact name to look up, typically the record's
                ``DisplayName`` field (the contact's display / trading name).
            refresh: When True, reload the company contact map from the API
                before looking up.

        Returns:
            The contact's ReLeased ``Id`` if found, otherwise ``None``.
        """
        if not display_name:
            return None
        if refresh or self._contacts_by_name is None:
            self._contacts_by_name = self._load_contacts_by_name()
        return self._contacts_by_name.get(display_name)

    def _load_contacts_by_name(self) -> dict[str, str]:
        """Page GET ``/contacts`` for this Company into a name→Id map.

        Used for create-time dedupe: without ``GlobalIdentifier``, match by name
        so we PUT with the existing contact ``Id`` instead of POSTing a duplicate.

        Indexes ``Name``, ``DisplayName``, and ``CorrespondenceName`` so an ETL
        ``DisplayName`` can match API ``Name`` when ``DisplayName`` is null.

        Returns:
            Mapping of contact name string → ReLeased contact ``Id``.
        """
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
    """Invoices sink — POST/PUT ``/invoices`` under the configured Company.

    Handles both sales invoices (``Type=ACCREC``) and bills (``Type=ACCPAY``).
    """

    name = "Invoices"
    endpoint = "/invoices"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Inject CompanyId on create when Id is absent.

        Args:
            record: Invoice/bill payload. ``Id`` is the ReLeased invoice id.
            context: SDK batch/context dict (unused here).

        Returns:
            Payload with ``CompanyId`` set when creating.
        """
        return self._with_company_id(record)

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Skip non-positive totals (API rejects them); otherwise use base upsert.

        Args:
            record: Invoice/bill payload after ``preprocess_record``.
            context: SDK batch/context dict (unused here).

        Returns:
            Tuple of ``(record_id, success, state_updates)``. Skipped rows use
            ``state_updates={"skipped": True}``.
        """
        total = self._invoice_total(record)
        if total <= 0:
            self.logger.info(
                "Skipping invoice with non-positive total=%s Reference=%s Type=%s",
                total,
                record.get("Reference"),
                record.get("Type"),
            )
            return record.get("Id") or record.get("Reference"), True, {"skipped": True}

        return super().upsert_record(record, context)

    @staticmethod
    def _invoice_total(record: dict) -> float:
        """Compute the invoice total for the non-positive skip check.

        Args:
            record: Invoice/bill payload with optional ``Total`` / ``SubTotal``
                or ``LineItems`` (``Quantity`` x ``UnitAmount``).

        Returns:
            Numeric total used to decide whether to skip the write.
        """
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
    """CreditNotes sink — POST/PUT ``/credit-notes`` under the configured Company."""

    name = "CreditNotes"
    endpoint = "/credit-notes"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Inject CompanyId on create when Id is absent.

        Args:
            record: Credit note payload. ``Id`` is the ReLeased credit note id.
            context: SDK batch/context dict (unused here).

        Returns:
            Payload with ``CompanyId`` set when creating.
        """
        return self._with_company_id(record)


class PaymentsSink(ReLeasedRecordSink):
    """Payments sink — POST ``/payments`` (create only)."""

    name = "Payments"
    endpoint = "/payments"

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Create a payment.

        Args:
            record: Payment payload for POST ``/payments``.
            context: SDK batch/context dict (unused here).

        Returns:
            Tuple of ``(payment_id, True, {})``.
        """
        response = self.request_api("POST", endpoint=self.endpoint, request_data=record)
        record_id = response.json()["Data"]["Id"]
        return record_id, True, {}


class CreditNoteAllocationSink(ReLeasedRecordSink):
    """CreditNoteAllocation sink — POST ``/credit-notes/{id}/allocations``.

    Applies a credit note to one or more invoices (allocation amounts).
    """

    name = "CreditNoteAllocation"
    endpoint = "/credit-notes/"

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """POST allocations for a credit note.

        Args:
            record: Must include ``CreditNoteId`` (ReLeased credit note ``Id``)
                and ``Allocations`` (list of allocation objects).
            context: SDK batch/context dict (unused here).

        Returns:
            Tuple of ``(allocation_or_credit_note_id, True, {})``.
        """
        credit_note_id = record["CreditNoteId"]
        payload = {"Allocations": record["Allocations"]}
        endpoint = f"{self.endpoint}{credit_note_id}/allocations"
        response = self.request_api("POST", endpoint=endpoint, request_data=payload)
        record_id = response.json().get("Data", {}).get("Id") or credit_note_id
        return record_id, True, {}
