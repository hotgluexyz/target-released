"""ReLeased target class."""

from __future__ import annotations

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from target_released.sinks import (
    ContactsSink,
    CreditNoteAllocationSink,
    CreditNotesSink,
    InvoicesSink,
    PaymentsSink,
)


class TargetReLeased(TargetHotglue):
    """Target for ReLeased."""

    name = "target-released"
    SINK_TYPES = [
        ContactsSink,
        CreditNotesSink,
        InvoicesSink,
        PaymentsSink,
        CreditNoteAllocationSink,
    ]

    config_jsonschema = th.PropertiesList(
        th.Property(
            "access_token",
            th.StringType,
            required=True,
            description="Long-lived Bearer access token for the Released API",
        ),
        th.Property(
            "company_id",
            th.StringType,
            required=True,
            description=(
                "ReLeased Company ID that scopes all target writes. "
                "Each Company has a name and ID; records are created under this Company."
            ),
        ),
    ).to_dict()


if __name__ == "__main__":
    TargetReLeased.cli()
