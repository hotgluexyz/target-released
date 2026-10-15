"""Persist QBD/contact name → Re-Leased ContactId as snapshots/contact_id_map.csv."""

from __future__ import annotations

import csv
from pathlib import Path

CONTACT_ID_MAP_CSV = "contact_id_map.csv"
CONTACT_ID_MAP_COLUMNS = ("name", "id")


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def contact_id_map_path(snapshot_dir: str) -> Path:
    """Return path to contact_id_map.csv under ``snapshot_dir``."""
    return Path(snapshot_dir) / CONTACT_ID_MAP_CSV


def read_contact_id_map(snapshot_dir: str) -> dict[str, str]:
    """Load contact_id_map.csv (columns name, id) from ``snapshot_dir``.

    Args:
        snapshot_dir: Directory containing snapshot CSVs.

    Returns:
        Mapping of name → Re-Leased ContactId. Empty if the file is missing.
    """
    path = contact_id_map_path(snapshot_dir)
    if not path.exists():
        return {}
    by_name: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = _str_or_none(row.get("name"))
            contact_id = _str_or_none(row.get("id"))
            if name and contact_id:
                by_name[name] = contact_id
    return by_name


def write_contact_id_map(snapshot_dir: str, by_name: dict[str, str]) -> None:
    """Write contact_id_map.csv under ``snapshot_dir``.

    Args:
        snapshot_dir: Directory containing snapshot CSVs.
        by_name: name → Re-Leased ContactId.
    """
    path = contact_id_map_path(snapshot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CONTACT_ID_MAP_COLUMNS))
        writer.writeheader()
        for name, contact_id in sorted(by_name.items(), key=lambda item: item[0].lower()):
            writer.writerow({"name": name, "id": contact_id})


def upsert_contact_id_map_entries(snapshot_dir: str, entries: dict[str, str]) -> dict[str, str]:
    """Merge name→Id entries into contact_id_map.csv and persist if changed.

    Args:
        snapshot_dir: Directory containing snapshot CSVs.
        entries: Names to set/update (e.g. DisplayName → ContactId).

    Returns:
        The full updated map.
    """
    by_name = read_contact_id_map(snapshot_dir)
    changed = False
    for name, contact_id in entries.items():
        clean_name = _str_or_none(name)
        clean_id = _str_or_none(contact_id)
        if not clean_name or not clean_id:
            continue
        if by_name.get(clean_name) != clean_id:
            by_name[clean_name] = clean_id
            changed = True
    if changed:
        write_contact_id_map(snapshot_dir, by_name)
    return by_name


def resolve_record_contact_id(record: dict, snapshot_dir: str) -> dict:
    """Ensure ``ContactId`` on an invoice/credit note; drop ``ContactName``.

    If ``ContactId`` is already set, ``ContactName`` is removed and the payload
    is returned. Otherwise looks up ``ContactName`` in contact_id_map.csv
    (populated by ContactsSink earlier in the same job on cold start).

    Args:
        record: Document payload that may include ContactId and/or ContactName.
        snapshot_dir: Directory containing contact_id_map.csv.

    Returns:
        Copy of ``record`` with ``ContactId`` set and ``ContactName`` removed.

    Raises:
        ValueError: Missing both id/name, or name not found in the map.
    """
    payload = dict(record)
    contact_name = _str_or_none(payload.pop("ContactName", None))
    if payload.get("ContactId"):
        return payload
    if not contact_name:
        msg = "Document missing ContactId and ContactName"
        raise ValueError(msg)
    contact_id = read_contact_id_map(snapshot_dir).get(contact_name)
    if not contact_id:
        msg = f"No ContactId in contact_id_map.csv for ContactName={contact_name!r}"
        raise ValueError(msg)
    payload["ContactId"] = contact_id
    return payload
