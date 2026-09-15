"""The data the numbers rest on: provenance checksums, complete fetches, and district names matched correctly."""
import hashlib
import json

import pandas as pd
import pytest

from crew.tools import ROOT
from metrics import run as metrics

OGD = ROOT / "data/external/ogd"


def test_official_tables_match_their_manifest():
    """F-50: every recorded csv_sha256 failed against its file, because text mode wrote CRLF on Windows."""
    manifest = json.loads((OGD / "_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 21
    for slug, entry in manifest.items():
        data = (OGD / f"{slug}.csv").read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["csv_sha256"], slug
        assert entry["records"] == entry["total_reported"], f"{slug} was fetched short"


def test_the_registry_export_matches_its_manifest():
    manifest = json.loads((ROOT / "data/registry/_manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        data = (ROOT / "data/registry" / name).read_bytes()
        assert len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"]


@pytest.mark.parametrize("state, slug, renamed", [
    ("Uttar Pradesh", "admissions_district_up_2021_22", {"Faizabad": "Ayodhya", "Allahabad": "Prayagraj"}),
    ("Gujarat", "admissions_district_gujarat_2023", {"Dohad": "Dahod", "Ahmadabad": "Ahmedabad"}),
])
def test_calibration_matches_every_published_district_to_the_right_one(state, slug, renamed):
    """F-47: fuzzy matching sent Faizabad (now Ayodhya) to Firozabad and dropped Allahabad and Dohad."""
    points = pd.read_csv(ROOT / "data/reference/district_points.csv", keep_default_na=False)
    frame = points[points.state.str.upper() == state.upper()]
    table = pd.read_csv(OGD / f"{slug}.csv", keep_default_na=False)
    names = table[~table.District.str.contains("Outside|Total", case=False)].District.tolist()
    codes = metrics.match_districts(names, frame)
    assert len(set(codes)) == len(names) == len(frame)                     # every district, each exactly once
    by_code = dict(zip(frame.district_code.astype(int), frame.district))
    for published, today in renamed.items():
        assert by_code[codes[names.index(published)]] == today


def test_an_unknown_district_name_fails_rather_than_guessing():
    frame = pd.DataFrame({"district": ["Firozabad", "Agra"], "district_code": [1, 2]})
    with pytest.raises(ValueError, match="unmatched"):
        metrics.match_districts(["Faizabadd"], frame.iloc[[1]])
    with pytest.raises(ValueError, match="matched twice"):
        metrics.match_districts(["Agra", "AGRA"], frame)


def test_the_unmatched_crosswalk_list_is_current():
    """F-49: a stale list named districts the crosswalk had long since resolved."""
    unmatched = pd.read_csv(ROOT / "data/crosswalk/unmatched_pmjay.csv", keep_default_na=False)
    mapped = pd.read_csv(ROOT / "data/crosswalk/name_map_pmjay.csv", keep_default_na=False)
    resolved = set(zip(mapped.src_state.str.upper(), mapped.src_district.str.upper()))
    assert not [p for p in zip(unmatched.src_state.str.upper(), unmatched.src_district.str.upper()) if p in resolved]
