"""Unit tests for ReLeased sink preprocess/upsert behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from hotglue_singer_sdk.exceptions import FatalAPIError

from target_released.client import format_released_api_error
from target_released.sinks import (
    ContactsSink,
    CreditNoteAllocationSink,
    CreditNotesSink,
    InvoicesSink,
    PaymentsSink,
    TrackingCategoriesSink,
    TrackingOptionsSink,
)
from target_released.target import TargetReLeased

COMPANY_ID = "3635ef5d-afc2-4227-a8a6-9fecd264a7de"


def _make_sink(sink_cls, request_api=None):
    target = TargetReLeased(config={"access_token": "tok", "company_id": COMPANY_ID})
    sink = sink_cls(
        target=target,
        stream_name=sink_cls.name,
        schema={"type": "object", "properties": {}},
        key_properties=[],
    )
    sink.request_api = request_api or MagicMock()
    return sink


def test_contacts_injects_company_id_on_create():
    sink = _make_sink(ContactsSink)
    payload = sink.preprocess_record({"DisplayName": "Ada"}, {})
    assert payload["CompanyId"] == COMPANY_ID


def test_contacts_skips_company_id_on_update():
    sink = _make_sink(ContactsSink)
    payload = sink.preprocess_record(
        {"DisplayName": "Ada", "GlobalIdentifier": "c-1"},
        {},
    )
    assert "CompanyId" not in payload


def test_contacts_maps_sdk_id_to_global_identifier():
    sink = _make_sink(ContactsSink)
    payload = sink.preprocess_record({"DisplayName": "Ada", "id": "released-1"}, {})
    assert payload["GlobalIdentifier"] == "released-1"
    assert "id" not in payload
    assert "CompanyId" not in payload


def test_contacts_name_lookup_loads_company_map_once():
    get_calls = {"n": 0}

    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            get_calls["n"] += 1
            response.json.return_value = {
                "Data": [
                    {"Id": "id-ada", "Name": "Ada", "DisplayName": "Ada"},
                    {"Id": "id-bob", "Name": "Bob", "DisplayName": "Bob"},
                ],
                "MetaData": {},
            }
        else:
            response.json.return_value = {"Data": {"Id": "unused"}}
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(ContactsSink, request_api=request_api)

    id_ada, _, state_ada = sink.upsert_record({"DisplayName": "Ada"}, {})
    id_bob, _, state_bob = sink.upsert_record({"DisplayName": "Bob"}, {})

    assert (id_ada, state_ada.get("is_updated")) == ("id-ada", True)
    assert (id_bob, state_bob.get("is_updated")) == ("id-bob", True)
    assert get_calls["n"] == 1


def test_contacts_global_identifier_export_does_not_block_company_map_load():
    get_calls = {"n": 0}

    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            get_calls["n"] += 1
            response.json.return_value = {
                "Data": [
                    {"Id": "id-bob", "Name": "Bob", "DisplayName": "Bob"},
                ],
                "MetaData": {},
            }
        else:
            response.json.return_value = {"Data": {"Id": "unused"}}
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(ContactsSink, request_api=request_api)

    id_ada, _, state_ada = sink.upsert_record(
        {"DisplayName": "Ada", "GlobalIdentifier": "id-ada"},
        {},
    )
    assert (id_ada, state_ada.get("is_updated")) == ("id-ada", True)
    assert sink._contacts_by_name is None  # noqa: SLF001
    assert sink._exported_contacts_by_name["Ada"] == "id-ada"  # noqa: SLF001
    assert get_calls["n"] == 0

    id_bob, _, state_bob = sink.upsert_record({"DisplayName": "Bob"}, {})
    assert (id_bob, state_bob.get("is_updated")) == ("id-bob", True)
    assert get_calls["n"] == 1
    assert sink._contacts_by_name == {"Bob": "id-bob", "Ada": "id-ada"}  # noqa: SLF001


def test_contacts_upsert_create_returns_data_id():
    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            response.json.return_value = {"Data": [], "MetaData": {}}
        else:
            response.json.return_value = {"Data": {"Id": "created-contact"}}
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(ContactsSink, request_api=request_api)

    record_id, success, state = sink.upsert_record({"DisplayName": "Ada"}, {})

    assert (record_id, success, state) == ("created-contact", True, {})
    assert request_api.call_args_list[-1].args[0] == "POST"


def test_contacts_upsert_updates_when_name_already_exists():
    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            response.json.return_value = {
                "Data": [{"Id": "existing-1", "Name": "Ada", "DisplayName": "Ada"}],
                "MetaData": {},
            }
        else:
            response.json.return_value = {"Data": {"Id": "existing-1"}}
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(ContactsSink, request_api=request_api)

    record = {"DisplayName": "Ada", "CompanyId": COMPANY_ID, "EmailAddress": "a@b.com"}
    record_id, success, state = sink.upsert_record(record, {})

    assert (record_id, success) == ("existing-1", True)
    assert state["is_updated"] is True
    put_calls = [c for c in request_api.call_args_list if c.args[0] == "PUT"]
    assert len(put_calls) == 1
    put_payload = put_calls[0].kwargs["request_data"]
    assert put_payload["GlobalIdentifier"] == "existing-1"
    assert "CompanyId" not in put_payload


def test_contacts_upsert_recovers_from_duplicate_name_error():
    calls = {"get": 0}

    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            calls["get"] += 1
            if calls["get"] == 1:
                response.json.return_value = {"Data": [], "MetaData": {}}
            else:
                response.json.return_value = {
                    "Data": [{"Id": "existing-2", "Name": "Ada"}],
                    "MetaData": {},
                }
            return response
        if method == "POST":
            raise FatalAPIError(
                '{"Errors":[{"Message":"Name - A contact with this name already exists"}]}'
            )
        response.json.return_value = {"Data": {"Id": "existing-2"}}
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(ContactsSink, request_api=request_api)

    record_id, success, state = sink.upsert_record(
        {"DisplayName": "Ada", "CompanyId": COMPANY_ID}, {}
    )

    assert (record_id, success, state.get("is_updated")) == ("existing-2", True, True)
    assert any(c.args[0] == "PUT" for c in request_api.call_args_list)


def test_invoices_upsert_create_only_skips_existing():
    response = MagicMock()
    response.json.return_value = {"Data": {"Id": "inv-1"}}
    request_api = MagicMock(return_value=response)
    sink = _make_sink(InvoicesSink, request_api=request_api)

    line_items = [
        {
            "Description": "Rent",
            "Quantity": 1,
            "UnitAmount": 10.0,
            "TaxType": "NT",
            "AccountCode": "4000",
        }
    ]
    create_payload = sink.preprocess_record(
        {
            "Type": "ACCREC",
            "ContactId": "c1",
            "LineItems": line_items,
        },
        {},
    )
    assert create_payload["CompanyId"] == COMPANY_ID
    record_id, _, state = sink.upsert_record(create_payload, {})
    assert record_id == "inv-1"
    assert state == {}
    assert request_api.call_args_list[-1].args[0] == "POST"

    prepared = sink.preprocess_record(
        {
            "id": "inv-1",
            "Type": "ACCREC",
            "ContactId": "c1",
            "LineItems": line_items,
        },
        {},
    )
    assert prepared["Id"] == "inv-1"
    record_id, _, state = sink.upsert_record(prepared, {})
    assert (record_id, state) == ("inv-1", {"existing": True})
    assert all(c.args[0] != "PUT" for c in request_api.call_args_list)


def test_invoices_have_contact_relation_fields():
    assert InvoicesSink.relation_fields == [
        {"field": "ContactId", "objectName": "Contacts"}
    ]
    assert CreditNotesSink.relation_fields == [
        {"field": "ContactId", "objectName": "Contacts"}
    ]


def test_invoices_skip_zero_or_negative_total(caplog):
    sink = _make_sink(InvoicesSink)
    record_id, success, state = sink.upsert_record(
        {
            "Type": "ACCREC",
            "Reference": "zero",
            "LineItems": [
                {
                    "Description": "x",
                    "Quantity": 1,
                    "UnitAmount": 0,
                    "TaxType": "NT",
                    "AccountCode": "4000",
                }
            ],
        },
        {},
    )
    assert success is True
    assert state.get("skipped") is True
    assert record_id == "zero"


def test_credit_notes_upsert_create_only_skips_existing():
    response = MagicMock()
    response.json.return_value = {"Data": {"Id": "cn-1"}}
    request_api = MagicMock(return_value=response)
    sink = _make_sink(CreditNotesSink, request_api=request_api)

    payload = sink.preprocess_record(
        {"ContactId": "c1", "Type": "ACCRECCREDIT", "LineItems": []},
        {},
    )
    assert payload["CompanyId"] == COMPANY_ID
    record_id, _, state = sink.upsert_record(payload, {})
    assert (record_id, state) == ("cn-1", {})
    assert request_api.call_args_list[-1].args[0] == "POST"

    prepared = sink.preprocess_record({"id": "cn-1", "ContactId": "c1"}, {})
    assert prepared["Id"] == "cn-1"
    record_id, _, state = sink.upsert_record(prepared, {})
    assert (record_id, state) == ("cn-1", {"existing": True})
    assert all(c.args[0] != "PUT" for c in request_api.call_args_list)


def test_payments_upsert_create_only_skips_existing():
    response = MagicMock()
    response.json.return_value = {"Data": {"Id": "pay-1"}}
    request_api = MagicMock(return_value=response)
    sink = _make_sink(PaymentsSink, request_api=request_api)

    record = {
        "InvoiceId": "inv-1",
        "AccountCode": "4000",
        "DateOfPayment": "2026-01-01",
        "Amount": 10.0,
        "Reference": "check-1",
    }
    payload = sink.preprocess_record(dict(record), {})
    assert "Id" not in payload
    record_id, success, state = sink.upsert_record(payload, {})
    assert (record_id, success, state) == ("pay-1", True, {})
    request_api.assert_called_once_with(
        "POST",
        endpoint="/payments",
        request_data=payload,
    )

    prepared = sink.preprocess_record({**record, "id": "pay-1"}, {})
    assert prepared["Id"] == "pay-1"
    record_id, _, state = sink.upsert_record(prepared, {})
    assert (record_id, state) == ("pay-1", {"existing": True})
    assert request_api.call_count == 1


def test_payments_parses_top_level_id_fallback():
    response = MagicMock()
    response.json.return_value = {"Id": "pay-top"}
    request_api = MagicMock(return_value=response)
    sink = _make_sink(PaymentsSink, request_api=request_api)

    record_id, success, state = sink.upsert_record(
        {
            "InvoiceId": "inv-1",
            "AccountCode": "4000",
            "DateOfPayment": "2026-01-01",
            "Amount": 10.0,
        },
        {},
    )
    assert (record_id, success, state) == ("pay-top", True, {})


def test_credit_note_allocation_nested_endpoint():
    response = MagicMock()
    response.json.return_value = {"Data": {"Id": "alloc-1"}}
    request_api = MagicMock(return_value=response)
    sink = _make_sink(CreditNoteAllocationSink, request_api=request_api)

    record = {
        "CreditNoteId": "cn-1",
        "Allocations": [
            {
                "InvoiceId": "inv-1",
                "Amount": 25.0,
                "AllocationDate": "2026-01-02",
            }
        ],
    }
    record_id, success, _ = sink.upsert_record(record, {})
    assert (record_id, success) == ("alloc-1", True)
    request_api.assert_called_once_with(
        "POST",
        endpoint="/credit-notes/cn-1/allocations",
        request_data={"Allocations": record["Allocations"]},
    )


def test_tracking_categories_registered_before_invoices():
    names = [cls.name for cls in TargetReLeased.SINK_TYPES]
    assert names.index("TrackingCategories") < names.index("TrackingOptions")
    assert names.index("TrackingOptions") < names.index("Invoices")


def test_tracking_categories_post_uses_company_id_query():
    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            response.json.return_value = {"Data": [], "MetaData": {}}
        else:
            response.json.return_value = {
                "Data": {"Id": "cat-1", "Name": "Class", "IsActive": True, "OptionCount": 0}
            }
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(TrackingCategoriesSink, request_api=request_api)

    payload = sink.preprocess_record({"Name": "Class"}, {})
    assert "CompanyId" not in payload
    record_id, success, state = sink.upsert_record(payload, {})
    assert (record_id, success, state) == ("cat-1", True, {})
    post = next(c for c in request_api.call_args_list if c.args[0] == "POST")
    assert post.kwargs["params"] == {"companyId": COMPANY_ID}
    assert post.kwargs["request_data"] == {"Name": "Class"}


def test_tracking_categories_skip_existing_name_case_insensitive():
    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        response.json.return_value = {
            "Data": [{"Id": "cat-exist", "Name": "class"}],
            "MetaData": {},
        }
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(TrackingCategoriesSink, request_api=request_api)
    record_id, _, state = sink.upsert_record({"Name": "Class"}, {})
    assert (record_id, state) == ("cat-exist", {"existing": True})
    assert all(c.args[0] != "POST" for c in request_api.call_args_list)


def test_tracking_options_post_with_parent():
    def side_effect(method, endpoint=None, params=None, request_data=None, **kwargs):
        response = MagicMock()
        if method == "GET":
            response.json.return_value = {"Data": [], "MetaData": {}}
        else:
            response.json.return_value = {"Data": {"Id": "opt-1", "Name": "BK-CHILD-CLASS"}}
        return response

    request_api = MagicMock(side_effect=side_effect)
    sink = _make_sink(TrackingOptionsSink, request_api=request_api)
    record_id, success, state = sink.upsert_record(
        {
            "Category": "Class",
            "Name": "BK-CHILD-CLASS",
            "Parent": "BK-Test-Class",
            "FullName": "BK-Test-Class:BK-CHILD-CLASS",
        },
        {},
    )
    assert (record_id, success, state) == ("opt-1", True, {})
    post = next(c for c in request_api.call_args_list if c.args[0] == "POST")
    assert post.kwargs["params"] == {"companyId": COMPANY_ID}
    assert post.kwargs["request_data"] == {
        "Category": "Class",
        "Name": "BK-CHILD-CLASS",
        "Parent": "BK-Test-Class",
    }


def test_format_released_api_error_prefers_errors_message():
    body = {
        "Title": "Bad request",
        "Detail": "",
        "Errors": [
            {
                "Code": "invoice_payment.payment_exceeds_payable_amount",
                "Message": "Payment of 14.00 exceeds amount due of 9.00 on Invoice X",
            }
        ],
    }
    response = MagicMock()
    response.text = json.dumps(body)
    response.json.return_value = body
    response.reason = "Bad Request"
    response.status_code = 400

    assert format_released_api_error(response) == (
        "invoice_payment.payment_exceeds_payable_amount: "
        "Payment of 14.00 exceeds amount due of 9.00 on Invoice X"
    )


def test_payments_surfaces_overpayment_error_with_record_context():
    request_api = MagicMock(
        side_effect=FatalAPIError(
            "invoice_payment.payment_exceeds_payable_amount: "
            "Payment of 14.00 exceeds amount due of 9.00 on Invoice X"
        )
    )
    sink = _make_sink(PaymentsSink, request_api=request_api)

    with pytest.raises(FatalAPIError) as exc_info:
        sink.upsert_record(
            {
                "InvoiceId": "inv-1",
                "AccountCode": "4000",
                "DateOfPayment": "2026-01-01",
                "Amount": 14.0,
                "Reference": "overpay",
                "externalId": "pay:inv-1",
            },
            {},
        )

    msg = str(exc_info.value)
    assert "Payment rejected" in msg
    assert "InvoiceId='inv-1'" in msg
    assert "Amount=14.0" in msg
    assert "invoice_payment.payment_exceeds_payable_amount" in msg
    assert "exceeds amount due of 9.00" in msg
