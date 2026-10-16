"""Basic target smoke tests."""

from __future__ import annotations

from target_released.target import TargetReLeased

SAMPLE_CONFIG = {
    "access_token": "test-token",
    "company_id": "00000000-0000-0000-0000-000000000000",
}


def test_target_instantiates_with_required_config() -> None:
    target = TargetReLeased(config=SAMPLE_CONFIG)
    assert target.name == "target-released"
    assert len(target.SINK_TYPES) == 5
