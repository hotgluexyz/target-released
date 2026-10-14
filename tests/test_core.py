"""Tests standard target features using the built-in SDK tests library."""

from __future__ import annotations

from typing import Any

import pytest
from hotglue_singer_sdk.testing import get_target_test_class

from target_released.target import TargetReLeased

SAMPLE_CONFIG: dict[str, Any] = {
    "access_token": "test-token",
    "company_id": "00000000-0000-0000-0000-000000000000",
}


# Run standard built-in target tests from the SDK:
StandardTargetTests = get_target_test_class(
    target_class=TargetReLeased,
    config=SAMPLE_CONFIG,
)


class TestTargetReLeased(StandardTargetTests):  # type: ignore[misc, valid-type] # ty: ignore[unsupported-base]
    """Standard Target Tests."""

    @pytest.fixture(scope="class")
    def resource(self):  # noqa: ANN201
        """Generic external resource.

        This fixture is useful for setup and teardown of external resources,
        such output folders, tables, buckets etc. for use during testing.

        Example usage can be found in the SDK samples test suite:
        https://github.com/hotgluexyz/HotglueSingerSDK
        """
        return "resource"


# TODO: Create additional tests as appropriate for your target.
