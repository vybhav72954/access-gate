"""Pull the official PM-JAY statistics this project cites from data.gov.in (OGD API).

    python scripts/fetch_ogd.py            # fetch anything not already on disk
    python scripts/fetch_ogd.py --refresh  # re-fetch everything

Needs DATA_GOV_IN_API_KEY in .env. The key is never written to disk outside
.env: request URLs in the manifest are recorded with the key redacted.

Every resource is a table the Ministry of Health tabled in Parliament and the
Rajya Sabha Secretariat published on data.gov.in. Each is fetched in full,
saved raw (JSON, with the API's field metadata) and as CSV with the published
column labels, and recorded in data/external/ogd/_manifest.json with its
catalogue dates, record count and a SHA-256 of the CSV.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/external/ogd"
API = "https://api.data.gov.in/resource/"
PAGE = 500

# slug -> (resource id, why this project needs it)
RESOURCES: dict[str, tuple[str, str]] = {
    # enforcement: what the scheme already does to hospitals
    "claims_nonadmissible_abuse_private": (
        "410b3af5-0f61-474b-94d9-ba6dbe00238b",
        "Claims found non-admissible for abuse, misuse or incorrect entries in private hospitals, by state"),
    "hospitals_deempanelled_by_ownership": (
        "bc39f5a9-eaec-417f-bde3-a5d2892622be",
        "Public vs private hospitals de-empanelled 2018-19 to 2024-25: whom enforcement falls on"),
    "hospitals_empanelled_by_ownership": (
        "ee5d9a44-d0bf-4e58-b2bd-410ab830f8d9",
        "Public vs private hospitals empanelled 2018-19 to 2024-25: denominators for de-empanelment rates"),
    "hospitals_opted_out": (
        "82572ca2-cf19-4b8e-bc4e-a2e0f2fe12ca",
        "Hospitals that left the scheme voluntarily 2019-20 to 2024-25: network loss that is not enforcement"),
    "beneficiaries_single_mobile": (
        "b63c7b68-8f0d-410c-b994-efc77fa81f58",
        "Beneficiaries linked to a single mobile number 2018-21 (the CAG identity finding)"),
    # volumes: how many claims the system would face
    "admissions_authorised_2019_2025": (
        "564d94de-0b69-4a85-9fbd-9972c51f7799",
        "Authorised hospital admissions by state 2019-20 to 2024-25: claim volume per state"),
    "admissions_2018_2025": (
        "e2292d55-71c5-416a-8a72-c7d63661b7a2",
        "Hospital admissions by state 2018-19 to 2024-25: cross-check of the series above"),
    "admissions_amount_2018_2025": (
        "c665d927-454f-4323-81d7-1b64b049c3ae",
        "Amount for hospital admissions by state 2018-19 to 2024-25: money at stake per state"),
    "admissions_count_amount_2021_2024": (
        "b4de3b6a-cfb2-423b-a04b-4b5cb8260140",
        "Admission count and amount by state 2021-22 to 2023-24: average claim value per state"),
    "claims_submitted_paid_pending": (
        "a7984e3b-c712-4aff-9d38-d46bc8f0be80",
        "Claims submitted, paid and pending by state 2020-21 to 2022-23: the payment pipeline"),
    "admissions_by_specialty_2024": (
        "1739b151-c629-4383-aa98-496c09ba3c0f",
        "Authorised admissions by specialty as on 30-06-2024: specialty mix of the claim corpus"),
    "admissions_by_specialty_2021": (
        "eb5def2c-a944-4518-b966-6877435fc395",
        "Admissions and amount by specialty (reply of 2 Feb 2021): second specialty mix, amounts"),
    "admissions_mental_health": (
        "7d71043c-1a45-412c-b649-e0f8c8d89a65",
        "Mental-health admissions by state (reply of 15 Mar 2022): the most concentrated specialty"),
    "beneficiaries_treated_since_inception": (
        "b1b803c9-e6d6-40b1-bd71-a17888fc5110",
        "Beneficiaries treated by state since inception to 2024-25: scale"),
    "treatment_expenditure_since_inception": (
        "62748b15-796c-427d-99db-667ca0ec8fd4",
        "Treatment expenditure by state since inception to 2024-25: scale"),
    "funds_released_2019_2025": (
        "2e6e5402-c86e-421e-a7e2-5e761251e3aa",
        "Funds released by state 2019-20 to 2024-25"),
    # network: validates the registry snapshot
    "hospitals_empanelled_2025_03": (
        "e7255d28-1378-4984-ae22-0784e4e0f599",
        "Empanelled hospitals by state as on 01-03-2025: validates the registry snapshot"),
    "hospitals_empanelled_ne_wb_2024": (
        "f5cddeb1-88c7-43fc-8a83-a216b7a3f3fd",
        "Public/private empanelled hospitals in the North-East and West Bengal as on 30-06-2024"),
    # the demo districts
    "admissions_district_up_2021_22": (
        "34f5b40a-5ca1-42e6-8523-5ea929684cc7",
        "District-wise authorised admissions in Uttar Pradesh 2021-22: Bahraich (S2)"),
    "admissions_district_gujarat_2023": (
        "a96f2450-dcac-4e83-98f6-8e5c4de39850",
        "District-wise authorised admissions in Gujarat (reply of 8 Aug 2023): Ahmedabad (S1)"),
    "hospitals_district_gujarat_2022": (
        "e58b952c-dbcf-472d-be05-3aaee16b2462",
        "District-wise eligible beneficiaries and empanelled hospitals in Gujarat (reply of 28 Mar 2022)"),
}


def _get(key: str, rid: str, offset: int) -> dict:
    q = urllib.parse.urlencode({"api-key": key, "format": "json", "offset": offset, "limit": PAGE})
    req = urllib.request.Request(f"{API}{rid}?{q}", headers={"User-Agent": "access-gate/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:                                   # transient OGD failures are common
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def fetch(key: str, slug: str, rid: str) -> dict:
    first = _get(key, rid, 0)
    if first.get("status") != "ok":
        raise RuntimeError(f"{slug}: {first.get('message')}")
    records, total = list(first.get("records", [])), int(first.get("total", 0))
    while len(records) < total:
        page = _get(key, rid, len(records)).get("records", [])
        if not page:
            break
        records += page
    if len(records) != total:
        # A page that came back empty used to end the loop and save the table short, silently (F-50).
        raise RuntimeError(f"{slug}: received {len(records)} of {total} records; nothing written")
    fields = first.get("field", [])
    labels = {f["id"]: f.get("name", f["id"]) for f in fields}

    raw = {k: first.get(k) for k in ("title", "desc", "org", "sector", "source", "catalog_uuid",
                                      "created_date", "updated_date", "field")}
    raw["records"] = records
    (OUT / f"{slug}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([labels[f["id"]] for f in fields])
    for r in records:
        w.writerow([r.get(f["id"], "") for f in fields])
    text = buf.getvalue()
    # newline="": the file holds exactly the bytes that are hashed. Text mode on Windows turned every "\n" into
    # "\r\n", so no recorded csv_sha256 matched its file (F-50).
    (OUT / f"{slug}.csv").write_text(text, encoding="utf-8", newline="")

    return {"resource_id": rid, "title": (first.get("title") or "").strip(), "org": first.get("org"),
            "source": first.get("source"), "catalog_created": first.get("created_date"),
            "catalog_updated": first.get("updated_date"), "records": len(records), "total_reported": total,
            "columns": [labels[f["id"]] for f in fields],
            "csv_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "request": f"{API}{rid}?api-key=<REDACTED>&format=json&limit={PAGE}",
            "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
    if not key:
        raise SystemExit("DATA_GOV_IN_API_KEY is not set in .env")

    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for slug, (rid, purpose) in RESOURCES.items():
        if slug in manifest and (OUT / f"{slug}.csv").exists() and not args.refresh:
            print(f"  cached   {slug}")
            continue
        entry = fetch(key, slug, rid)
        entry["purpose"] = purpose
        manifest[slug] = entry
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  fetched  {slug:40} {entry['records']:>4} rows  {len(entry['columns'])} cols")
        time.sleep(0.5)
    blob = manifest_path.read_text(encoding="utf-8")
    assert key not in blob, "API key leaked into the manifest"
    print(f"{len(manifest)} resources in {manifest_path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
