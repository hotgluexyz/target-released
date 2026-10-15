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
from target_released.snapshot_upload import ensure_reference_data_marker


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
            "api_url",
            th.StringType,
            description="Base URL for the Released API",
            default="https://api.re-leased.com/api",
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
        th.Property(
            "snapshot_dir",
            th.StringType,
            default="snapshots",
            description=(
                "Flow-level snapshot directory for CSVs such as contact_id_map.csv. "
                "Defaults to 'snapshots' (hotglue job/flow working dir); "
                "hotglue may inject an absolute path at runtime."
            ),
        ),
    ).to_dict()

    def __init__(self, *args, **kwargs) -> None:
        """Initialize target and ensure post-export snapshot upload marker."""
        super().__init__(*args, **kwargs)
        snapshot_dir = self.config.get("snapshot_dir") or "snapshots"
        ensure_reference_data_marker(snapshot_dir)


if __name__ == "__main__":
    TargetReLeased.cli()
