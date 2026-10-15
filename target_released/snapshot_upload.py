"""Ensure hotglue re-uploads snapshots after target export.

The executor only uploads ``{tenant}/snapshots`` after ETL by default. After
export it uploads again only when ``reference_data.json`` exists under the
local snapshots dir (see hotglue ``docker/executor/lib/target.py``). This
marker triggers that upload so ``contact_id_map.csv`` / ``document_id_map.csv``
written during export are persisted.
"""

from __future__ import annotations

from pathlib import Path

REFERENCE_DATA_JSON = "reference_data.json"


def ensure_reference_data_marker(snapshot_dir: str) -> Path:
    """Create an empty ``reference_data.json`` in ``snapshot_dir`` if missing.

    Args:
        snapshot_dir: Local snapshots directory (injected by hotglue as
            ``snapshot_dir`` in target config).

    Returns:
        Path to the marker file.
    """
    path = Path(snapshot_dir) / REFERENCE_DATA_JSON
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    return path
