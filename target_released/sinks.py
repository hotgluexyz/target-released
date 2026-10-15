"""ReLeased target sinks. Records are scoped to a Company (``company_id``)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from hotglue_singer_sdk.exceptions import FatalAPIError

from target_released.client import ReLeasedRecordSink
from target_released.contact_id_map import (
    resolve_record_contact_id,
    upsert_contact_id_map_entries,
)
from target_released.document_id_map import upsert_document_id_map_entries

# Flush contact_id_map.csv after this many successful contact exports.
_CONTACT_ID_MAP_BATCH_SIZE = 10

# UpdateInvoiceRequest fields (swagger) — omit Type / CompanyId on PUT.
_UPDATE_INVOICE_KEYS = (
    "Id",
    "ContactId",
    "IssueDate",
    "DueDate",
    "Status",
    "LineAmountTypes",
    "LineItems",
    "PropertyId",
    "TenancyId",
    "Reference",
    "ServiceDate",
)
_UPDATE_INVOICE_LINE_KEYS = (
    "Id",
    "Description",
    "Quantity",
    "UnitAmount",
    "TaxType",
    "AccountCode",
)


class ContactsSink(ReLeasedRecordSink):
    """Contacts sink — POST/PUT ``/contacts`` under the configured Company.

    On write, ReLeased identifies an existing contact with ``GlobalIdentifier``,
    which is the contact's API ``Id`` (from GET ``/contacts`` ``Data[].Id``).
    After successful exports, ``DisplayName`` / ``CorrespondenceName`` → Id are
    batched (every 10 contacts, plus stream ``clean_up``) into
    ``snapshots/contact_id_map.csv``.
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
        self._exported_contacts_by_name: dict[str, str] = {}
        self._pending_contact_id_map: dict[str, str] = {}
        self._pending_contact_exports = 0

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

    def clean_up(self) -> None:
        """Flush any remaining batched contact_id_map.csv updates."""
        self._flush_contact_id_map()
        super().clean_up()

    def _remember_exported_contact(self, payload: dict, contact_id: str) -> None:
        """Update in-memory cache; batch contact_id_map.csv writes.

        Entries are held until ``_CONTACT_ID_MAP_BATCH_SIZE`` contacts have been
        exported, then merged into the CSV in one write. Remaining entries are
        flushed in ``clean_up``.

        This is to increase efficiency when updating the contact_id_map.csv file.

        Args:
            payload: Contact payload that was written (uses DisplayName /
                CorrespondenceName as map keys).
            contact_id: ReLeased contact ``Id`` returned from create/update.
        """
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
        self._pending_contact_id_map.update(entries)
        self._pending_contact_exports += 1
        if self._pending_contact_exports >= _CONTACT_ID_MAP_BATCH_SIZE:
            self._flush_contact_id_map()

    def _flush_contact_id_map(self) -> None:
        """Merge pending name→Id entries into contact_id_map.csv and clear the batch."""
        if not self._pending_contact_id_map:
            return

        snapshot_dir = self.config.get("snapshot_dir") or "snapshots"
        entries = self._pending_contact_id_map
        upsert_contact_id_map_entries(snapshot_dir, entries)
        self.logger.info(
            "Updated contact_id_map.csv in %s for %s contact(s) (%s name keys)",
            snapshot_dir,
            self._pending_contact_exports,
            len(entries),
        )
        self._pending_contact_id_map = {}
        self._pending_contact_exports = 0

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
            loaded = self._load_contacts_by_name()
            loaded.update(self._exported_contacts_by_name)
            self._exported_contacts_by_name.clear()
            self._contacts_by_name = loaded
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
    Resolves ``ContactId`` from ``ContactName`` via contact_id_map.csv after
    Contacts have been exported in the same job. After a successful export,
    QBD ``TxnID`` → ReLeased ``Id`` is written to ``document_id_map.csv``.
    """

    name = "Invoices"
    endpoint = "/invoices"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Inject CompanyId and resolve ContactId from the contact map.

        Leaves ``TxnID`` on the payload for ``upsert_record`` (stripped before
        the API call). ``Id`` is set by ETL when the document map has a hit.

        Args:
            record: Invoice/bill payload. May include ``ContactName`` / ``TxnID``
                from ETL and/or ``ContactId`` / ``Id``.
            context: SDK batch/context dict (unused here).

        Returns:
            Payload ready for upsert (ContactName removed; ContactId set).
        """
        payload = self._with_company_id(record)
        snapshot_dir = self.config.get("snapshot_dir") or "snapshots"
        return resolve_record_contact_id(payload, snapshot_dir)

    def upsert_record(self, record: dict, context: dict) -> tuple[Any, bool, dict]:
        """Skip non-positive totals; POST create or PUT update; persist Id map.

        Args:
            record: Invoice/bill payload after ``preprocess_record``.
            context: SDK batch/context dict (unused here).

        Returns:
            Tuple of ``(record_id, success, state_updates)``. Skipped rows use
            ``state_updates={"skipped": True}``.
        """
        txn_id = record.pop("TxnID", None)
        total = self._invoice_total(record)
        if total <= 0:
            self.logger.info(
                "Skipping invoice with non-positive total=%s Reference=%s Type=%s",
                total,
                record.get("Reference"),
                record.get("Type"),
            )
            return record.get("Id") or record.get("Reference"), True, {"skipped": True}

        state_updates: dict = {}
        if record.get("Id"):
            method = "PUT"
            state_updates["is_updated"] = True
            request_data = self._invoice_update_payload(record)
        else:
            method = "POST"
            request_data = record

        response = self.request_api(method, endpoint=self.endpoint, request_data=request_data)
        record_id = record["Id"] if method == "PUT" else response.json()["Data"]["Id"]
        if txn_id and record_id:
            snapshot_dir = self.config.get("snapshot_dir") or "snapshots"
            upsert_document_id_map_entries(snapshot_dir, {txn_id: str(record_id)})
            self.logger.info(
                "Updated document_id_map.csv in %s for TxnID=%s → %s",
                snapshot_dir,
                txn_id,
                record_id,
            )
        return record_id, True, state_updates

    @staticmethod
    def _invoice_update_payload(record: dict) -> dict:
        """Build UpdateInvoiceRequest body (no Type / CompanyId)."""
        payload = {
            key: record[key]
            for key in _UPDATE_INVOICE_KEYS
            if key in record and record[key] is not None
        }
        lines = payload.get("LineItems")
        if isinstance(lines, list):
            payload["LineItems"] = [
                {
                    key: line[key]
                    for key in _UPDATE_INVOICE_LINE_KEYS
                    if key in line and line[key] is not None
                }
                for line in lines
                if isinstance(line, dict)
            ]
        return payload

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
    """CreditNotes sink — POST/PUT ``/credit-notes`` under the configured Company.

    Resolves ``ContactId`` from ``ContactName`` via contact_id_map.csv after
    Contacts have been exported in the same job.
    """

    name = "CreditNotes"
    endpoint = "/credit-notes"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Inject CompanyId and resolve ContactId from the contact map.

        Args:
            record: Credit note payload. May include ``ContactName`` from ETL
                and/or ``ContactId``. ``Id`` is the ReLeased credit note id.
            context: SDK batch/context dict (unused here).

        Returns:
            Payload ready for upsert (ContactName removed; ContactId set).
        """
        payload = self._with_company_id(record)
        snapshot_dir = self.config.get("snapshot_dir") or "snapshots"
        return resolve_record_contact_id(payload, snapshot_dir)


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
        record_id = response.json()["Id"]
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
