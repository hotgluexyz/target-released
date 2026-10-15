"""Persist QBD TxnID → Re-Leased invoice Id as snapshots/document_id_map.csv."""

from __future__ import annotations

import csv
from pathlib import Path

DOCUMENT_ID_MAP_CSV = "document_id_map.csv"
DOCUMENT_ID_MAP_COLUMNS = ("txn_id", "id")


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def document_id_map_path(snapshot_dir: str) -> Path:
    """Return path to document_id_map.csv under ``snapshot_dir``."""
    return Path(snapshot_dir) / DOCUMENT_ID_MAP_CSV


def read_document_id_map(snapshot_dir: str) -> dict[str, str]:
    """Load document_id_map.csv (columns txn_id, id) from ``snapshot_dir``.

    Args:
        snapshot_dir: Directory containing snapshot CSVs.

    Returns:
        Mapping of QBD TxnID → Re-Leased invoice Id. Empty if missing.
    """
    path = document_id_map_path(snapshot_dir)
    if not path.exists():
        return {}
    by_txn: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            txn_id = _str_or_none(row.get("txn_id"))
            doc_id = _str_or_none(row.get("id"))
            if txn_id and doc_id:
                by_txn[txn_id] = doc_id
    return by_txn


def write_document_id_map(snapshot_dir: str, by_txn: dict[str, str]) -> None:
    """Write document_id_map.csv under ``snapshot_dir``.

    Args:
        snapshot_dir: Directory containing snapshot CSVs.
        by_txn: QBD TxnID → Re-Leased invoice Id.
    """
    path = document_id_map_path(snapshot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(DOCUMENT_ID_MAP_COLUMNS))
        writer.writeheader()
        for txn_id, doc_id in sorted(by_txn.items(), key=lambda item: item[0].lower()):
            writer.writerow({"txn_id": txn_id, "id": doc_id})


def upsert_document_id_map_entries(snapshot_dir: str, entries: dict[str, str]) -> dict[str, str]:
    """Merge TxnID→Id entries into document_id_map.csv and persist if changed.

    Args:
        snapshot_dir: Directory containing snapshot CSVs.
        entries: TxnIDs to set/update.

    Returns:
        The full updated map.
    """
    by_txn = read_document_id_map(snapshot_dir)
    changed = False
    for txn_id, doc_id in entries.items():
        clean_txn = _str_or_none(txn_id)
        clean_id = _str_or_none(doc_id)
        if not clean_txn or not clean_id:
            continue
        if by_txn.get(clean_txn) != clean_id:
            by_txn[clean_txn] = clean_id
            changed = True
    if changed:
        write_document_id_map(snapshot_dir, by_txn)
    return by_txn
