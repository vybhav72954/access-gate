"""Close the three data gaps the audit found. No network required.

    python scripts/build_lookups.py

Writes  data/reference/district_points.csv   -> unblocks triggers T5 and T6
        data/reference/triggers.json         -> backs trigger_guidance()
Checks  the Paediatric Cancer cells survived into district_adequacy.csv (D-5)

Why district_points rather than a distance matrix: 734 districts is 268,861
pairs. Storing centroids (734 rows) and computing haversine on demand is O(1),
exact, and 400x smaller.
"""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pandas as pd
from shapely.geometry import shape

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data/reference"
GEO = ROOT / "data/geo/district_boundaries.geojson"
GUIDE = ROOT / "rulebooks/NHA_AntiFraud_Guidebook.txt"

# ── 1 · district points ────────────────────────────────────────────────────


def build_points() -> pd.DataFrame:
    gj = json.loads(GEO.read_text(encoding="utf-8"))
    rows = []
    for ft in gj["features"]:
        p = ft["properties"]
        if p.get("district_code") is None:
            continue
        pt = shape(ft["geometry"]).representative_point()
        rows.append((int(p["district_code"]), p["district_name_english"],
                     p["state_name_english"], round(pt.y, 6), round(pt.x, 6)))
    df = pd.DataFrame(rows, columns=["district_code", "district", "state", "lat", "lon"])
    feat = pd.read_csv(ROOT / "data/district/features_by_district.csv")
    feat["pop_now"] = (10 ** feat["log_pop_now"]).round().astype("Int64")
    df = df.merge(feat[["district_code", "pop_now", "is_post2011_district"]],
                  on="district_code", how="left")
    df.to_csv(REF / "district_points.csv", index=False)
    print(f"  district_points.csv       {len(df)} districts")
    return df


# ── 2 · trigger catalogue ──────────────────────────────────────────────────

CHANNEL_CUES = {
    "desk_audit":        r"desk audit",
    "hospital_visit":    r"hospital visit",
    "beneficiary_call":  r"beneficiary call",
    "beneficiary_visit": r"beneficiary visit",
}

# severity is ours, not NHA's -- recorded here so policy.py can cite a single source
SEVERITY = {2: 2, 3: 2, 4: 1, 5: 3, 6: 3, 7: 2, 10: 3}


def build_triggers() -> list[dict]:
    raw = GUIDE.read_text(encoding="utf-8")
    t = re.sub(r"\s+", " ", raw)
    i = t.find("High number of cases for zero Length of Stay")
    seg = t[i - 400: i + 120000] if i > 0 else t

    parts = re.split(
        r"(?<=[a-z\)])\s(?=\d{1,2}\s+(?:High|Low|Multiple|Repeat|Unusual|Zero|Same|"
        r"Duplicate|Death|Gender|Excess|Cases|Claim|Highlight))", seg)

    out = []
    for part in parts[1:]:
        m = re.match(r"(\d{1,2})\s+(.{20,190}?)\s+1\.\s", part)
        if not m:
            continue
        tid, name = int(m.group(1)), " ".join(m.group(2).split())
        body = part[:6000]
        channels = [k for k, cue in CHANNEL_CUES.items() if re.search(cue, body, re.I)]
        checks = [re.sub(r"\s+\d{1,3}\s+Annexure\s+\d.*$", "", " ".join(c.split()))[:190]   # drop PDF page footers
                  for c in re.findall(r"\d\.\s([^0-9][^.]{18,190}?)(?=\s\d\.\s|$)", body)][:8]
        out.append({
            "trigger_id": tid,
            "name": name,
            "severity": SEVERITY.get(tid, 2),
            "channels": channels or ["desk_audit"],
            "checklist": checks,
            "source": "NHA Anti-Fraud Framework Practitioners' Guidebook, Annexure 2",
        })

    # the three checks we derive from published data rather than the guidebook
    out += [
        # Severity 2, as in rules/triggers.py: CAG 11/2023 §4.5 documents hospitals delivering specialties
        # they were capable of but never applied for (LLD B-5). This file is read for checklists only.
        {"trigger_id": "R1", "name": "Package billed for a specialty the hospital is not empanelled for",
         "severity": 2, "channels": ["desk_audit"],
         "checklist": ["Compare package specialty against the hospital's registry specialty list"],
         "source": "Derived: PM-JAY empanelled hospital registry"},
        {"trigger_id": "R2", "name": "Claimed amount exceeds the published package rate",
         "severity": 2, "channels": ["desk_audit"],
         "checklist": ["Compare amount_claimed against package_amount_rs",
                       "Check whether an implant add-on is separately justified"],
         "source": "Derived: Punjab SHA published HBP package master"},
        {"trigger_id": "R3", "name": "Government-reserved package billed by a private hospital",
         "severity": 2, "channels": ["desk_audit"],
         "checklist": ["Check govt_reserved flag against hospital type",
                       "Check whether a referral was recorded"],
         "source": "Derived: Punjab SHA published HBP package master"},
    ]

    (REF / "triggers.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    nha = [t for t in out if isinstance(t["trigger_id"], int)]
    print(f"  triggers.json             {len(out)} triggers "
          f"({len(nha)} from the guidebook, 3 derived)")
    for t in out:
        print(f"     {str(t['trigger_id']):<3} sev{t['severity']}  "
              f"{','.join(t['channels'])[:38]:<40} {t['name'][:52]}")
    return out


# ── 3 · hygiene ────────────────────────────────────────────────────────────


def check_adequacy() -> None:
    """DO NOT 'clean' nulls out of the reference tables.

    `NA` is a real registry code -- Paediatric Cancer -- and pandas silently converts the string "NA" to NaN
    on read. An earlier version of this script deleted 50 legitimate Paediatric Cancer cells believing they
    were nulls (data defect D-5). Since v2 the tables store canonical names (`paediatric_cancer`), and
    build_reference.py asserts the raw code survives the registry read; this check asserts the cells
    survived into district_adequacy.csv. Every consumer still reads with keep_default_na=False, because
    `NA` also appears as "not applicable" text in hbp_package_rates.csv.
    """
    p = REF / "district_adequacy.csv"
    good = pd.read_csv(p, keep_default_na=False)
    canonical = set(pd.read_csv(REF / "specialty_canonical.csv", keep_default_na=False).specialty)
    blank = good.specialty.astype(str).str.strip().eq("")
    unknown = set(good.specialty) - canonical
    paediatric_cancer = int((good.specialty == "paediatric_cancer").sum())
    print(f"  district_adequacy.csv     {len(good):,} cells, {paediatric_cancer} Paediatric Cancer cells present")
    assert not blank.any(), "blank specialty found in district_adequacy.csv"
    assert not unknown, f"non-canonical specialties in district_adequacy.csv: {sorted(unknown)}"
    assert paediatric_cancer >= 250, "Paediatric Cancer cells were lost - the NA landmine went off again (D-5)"


def main() -> None:
    print("closing audit gaps\n")
    build_points()
    build_triggers()
    check_adequacy()
    print("\n  T5 and T6 are now constructible: district_points.csv + haversine gives")
    print("  the distance between ANY two districts, not just sole-provider cells.")


if __name__ == "__main__":
    main()
