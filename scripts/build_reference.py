"""Build the precomputed reference tables the crew's tools read.

    python scripts/build_reference.py

Reads   data/registry/, data/crosswalk/, data/district/, data/geo/
Writes  data/reference/specialty_canonical.csv
        data/reference/district_adequacy.csv
        data/reference/access_distance.csv
        data/reference/district_network.csv
        data/reference/figures.json          <- every number quoted in docs/

Every figure quoted in docs/ comes from this script, and the script ASSERTS
them. If a filter changes and a number drifts -- or the demo case stops being
true -- the run fails instead of letting the report and the code disagree
(risk R-10).

v2, 16 Sep 2026 -- specialties are CANONICAL. The registry records one
specialty under two code vintages: HBP 1.0 `S12` and HBP 2022 `MC` both mean
Cardiology, and hospitals list one or the other, almost never both. v1 counted
them as different specialties, which split a district's providers across two
cells and overstated every sole-provider figure: the sole-provider share fell
from 25.6% to 19.1%, and Gaya -- v1's demo case -- turned out to have three
cardiology providers, not one. See data defect D-6.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import shape

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
REG  = ROOT / "data/registry/PMJAY_empanelled_hospitals_2026-07-16.xls"
XW   = ROOT / "data/crosswalk/name_map_pmjay.csv"
FEAT = ROOT / "data/district/features_by_district.csv"
ASP  = ROOT / "data/district/niti_aspirational_districts.csv"
GEO  = ROOT / "data/geo/district_boundaries.geojson"
OUT  = ROOT / "data/reference"

# ── filters · docs/04-DATA-DICTIONARY.md §2.2 ──────────────────────────────
EMPANELMENT_SCOPE = "PMJAY"
EXCLUDE_STATES = {"DELHI", "NCT OF DELHI", "ODISHA", "WEST BENGAL"}
# Never implemented PM-JAY. Left in, they read as catastrophic gaps (C-01).

EARTH_R_KM = 6371.0

# ── canonical specialties ──────────────────────────────────────────────────
# id, display name, registry codes (both vintages), tertiary
SPECIALTIES = [
    ("general_medicine",              "General Medicine",                 ("M1", "MG"),  False),
    ("paediatric_medicine",           "Paediatric Medical Management",    ("M2", "MP"),  False),
    ("neonatal",                      "Neo-natal Care",                   ("M3", "MN"),  False),
    ("paediatric_cancer",             "Paediatric Cancer",                ("M4", "NA"),  False),
    ("medical_oncology",              "Medical Oncology",                 ("M5", "MO"),  False),
    ("radiation_oncology",            "Radiation Oncology",               ("M6", "MR"),  True),
    ("emergency_room",                "Emergency Room Packages",          ("M7", "ER"),  False),
    ("mental_disorders",              "Mental Disorders",                 ("M8", "MM"),  False),
    ("general_surgery",               "General Surgery",                  ("S1", "SG"),  False),
    ("ent",                           "Otorhinolaryngology (ENT)",        ("S2", "SL"),  False),
    ("ophthalmology",                 "Ophthalmology",                    ("S3", "SE"),  False),
    ("obgyn",                         "Obstetrics & Gynaecology",         ("S4", "SO"),  False),
    ("orthopaedics",                  "Orthopaedics",                     ("S5", "SB"),  False),
    ("polytrauma",                    "Polytrauma",                       ("S6", "ST"),  False),
    ("urology",                       "Urology",                          ("S7", "SU"),  False),
    ("neurosurgery",                  "Neurosurgery",                     ("S8", "SN"),  True),
    ("interventional_neuroradiology", "Interventional Neuroradiology",    ("S9", "IN"),  True),
    ("plastic_surgery",               "Plastic & Reconstructive Surgery", ("S10", "SP"), False),
    ("burns",                         "Burns Management",                 ("S11", "BM"), False),
    ("cardiology",                    "Cardiology",                       ("S12", "MC"), True),
    ("ctvs",                          "Cardio-thoracic & Vascular Surgery", ("S13", "SV"), True),
    ("paediatric_surgery",            "Paediatric Surgery",               ("S14", "SS"), False),
    ("surgical_oncology",             "Surgical Oncology",                ("S15", "SC"), False),
    ("oral_maxillofacial",            "Oral & Maxillofacial Surgery",     ("S16", "SM"), False),
]
CODE_TO_SPEC = {code: sid for sid, _, codes, _ in SPECIALTIES for code in codes}
DISPLAY = {sid: name for sid, name, _, _ in SPECIALTIES}
TERTIARY = {sid for sid, _, _, tert in SPECIALTIES if tert}

# Codes deliberately NOT scored. Diagnostic/OPD categories are not inpatient
# specialties; JR/TG/OT exist in too few districts for adequacy to mean anything.
UNSCORED = {"ID": "diagnostic - infectious disease tests", "M10": "OPD diagnostic",
            "CP": "consultations & procedures", "OC": "OPD consultations",
            "JR": "rare - joint replacement", "TG": "rare - gender affirming treatment",
            "OT": "rare - organ & tissue transplant"}

BASIC_TIER = r"\bCHC\b|\bPHC\b|COMMUNITY HEALTH|PRIMARY HEALTH"
BULK_STATES = {"PUNJAB", "GUJARAT", "TELANGANA"}

# ── the demo case the docs rest on — asserted, never assumed ───────────────
DEMO_ESCALATE = ("Bahraich", "Uttar Pradesh", "cardiology")
DEMO_CONTRAST = ("Ahmedabad", "Gujarat", "cardiology")


def load_hospitals() -> pd.DataFrame:
    h = pd.read_excel(REG)
    xw = pd.read_csv(XW)
    for c in ("src_state", "src_district"):
        xw[c] = xw[c].astype(str).str.strip().str.upper()
    h["src_state"] = h["State"].astype(str).str.strip().str.upper()
    h["src_district"] = h["District"].astype(str).str.strip().str.upper()

    n_raw = len(h)
    h = h.merge(xw[["src_state", "src_district", "district_code"]],
                on=["src_state", "src_district"], how="left")
    placed = h["district_code"].notna()
    rate = placed.mean()
    print(f"  placed on LGD frame        {placed.sum():,} / {n_raw:,} ({rate*100:.1f}%)")
    assert rate > 0.99, f"crosswalk placement fell to {rate:.3f} (risk R-13)"

    h = h[placed].copy()
    h["district_code"] = h["district_code"].astype(int)
    h = h[h["Empanelment Type"].astype(str).str.contains(EMPANELMENT_SCOPE, na=False)]
    h = h[~h["src_state"].isin(EXCLUDE_STATES)].copy()
    h["basic_tier"] = h["Hospital Name"].str.upper().str.contains(BASIC_TIER, na=False, regex=True)
    print(f"  PMJAY-scope, participating {len(h):,}")
    return h


def registry_codes(cell) -> list[str]:
    if pd.isna(cell):
        return []
    return sorted({t.strip() for t in str(cell).replace(" ", "").split(",")
                   if t.strip() and t.strip() != "-NA-"})


def hospital_specialties(h: pd.DataFrame) -> pd.DataFrame:
    """One row per hospital x canonical specialty. Vintage duplicates collapse."""
    long = (h.assign(code=h["Current Specialities"].map(registry_codes))
             .explode("code").dropna(subset=["code"]))
    unknown = set(long.code) - set(CODE_TO_SPEC) - set(UNSCORED)
    assert not unknown, f"unmapped specialty codes: {sorted(unknown)}"
    assert "NA" in set(long.code), "code 'NA' (Paediatric Cancer) was lost on the way in (D-5)"

    long["specialty"] = long.code.map(CODE_TO_SPEC)
    scored = long.dropna(subset=["specialty"])
    hs = (scored[["Hospital Id", "Hospital Type", "src_state", "district_code", "basic_tier", "specialty"]]
            .drop_duplicates(["Hospital Id", "specialty"]))
    n_none = int((h["Current Specialities"].map(registry_codes).map(len) == 0).sum())
    print(f"  hospitals listing nothing  {n_none:,} (D-4)")
    print(f"  hospital x specialty slots {len(hs):,}  across {hs.specialty.nunique()} canonical specialties")
    return hs


def build_adequacy(hs: pd.DataFrame, aset: set[int]) -> pd.DataFrame:
    def count(t):
        return lambda s: int((s == t).sum())
    cell = (hs.groupby(["district_code", "specialty"])
              .agg(n_providers=("Hospital Id", "nunique"),
                   n_public=("Hospital Type", count("Public")),
                   n_private=("Hospital Type", count("Private(For Profit)")),
                   n_nfp=("Hospital Type", count("Private(Not For Profit)")),
                   n_goi=("Hospital Type", count("GOI")),
                   n_basic_tier=("basic_tier", "sum"))
              .reset_index())
    cell["specialty_name"] = cell.specialty.map(DISPLAY)
    cell["tertiary"] = cell.specialty.isin(TERTIARY)
    cell["aspirational"] = cell.district_code.isin(aset)
    print(f"  occupied cells             {len(cell):,}")
    return cell


def district_points() -> pd.DataFrame:
    gj = json.loads(GEO.read_text(encoding="utf-8"))
    rows = []
    for ft in gj["features"]:
        p = ft["properties"]
        if p.get("district_code") is None:
            continue
        pt = shape(ft["geometry"]).representative_point()
        rows.append((int(p["district_code"]), p["district_name_english"],
                     p["state_name_english"], pt.y, pt.x))
    return pd.DataFrame(rows, columns=["district_code", "district", "state", "lat", "lon"])


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def build_access_distance(cell, pts, feat) -> pd.DataFrame:
    """Per sole-provider cell: nearest OTHER district offering the specialty.

    Straight-line between representative points; road travel is ~1.3-1.5x
    further, so every figure is conservative.
    """
    pts = pts.reset_index(drop=True)
    idx = {c: i for i, c in enumerate(pts.district_code)}
    LAT, LON, CODES = pts.lat.values, pts.lon.values, pts.district_code.values

    out = []
    for spec, grp in cell.groupby("specialty"):
        have = np.array([idx[d] for d in grp.district_code if d in idx])
        if len(have) < 2:
            continue
        for d in grp.loc[grp.n_providers == 1, "district_code"]:
            if d not in idx:
                continue
            others = have[have != idx[d]]
            dist = haversine(LAT[idx[d]], LON[idx[d]], LAT[others], LON[others])
            j = int(np.argmin(dist))
            out.append((d, spec, float(dist[j]), int(CODES[others[j]])))

    acc = pd.DataFrame(out, columns=["district_code", "specialty", "km_to_alternative",
                                     "nearest_alternative_code"])
    names = pts.set_index("district_code")
    acc["district"] = acc.district_code.map(names.district)
    acc["state"] = acc.district_code.map(names.state)
    acc["nearest_alternative"] = acc.nearest_alternative_code.map(names.district)
    acc = acc.merge(feat[["district_code", "pop_now", "aspirational", "is_post2011_district"]],
                    on="district_code", how="left")
    print(f"  sole-provider cells w/ km  {len(acc):,}")
    return acc


def compute_figures(h, hs, cell, acc, feat) -> dict:
    km = acc.km_to_alternative
    sole = cell[cell.n_providers == 1]
    f: dict = {
        "hospitals_scope": int(len(h)),
        "cells": int(len(cell)),
        "sole_cells": int(len(sole)),
        "sole_share_pct": round(len(sole) / len(cell) * 100, 1),
        "le2_share_pct": round((cell.n_providers <= 2).mean() * 100, 1),
        "le5_share_pct": round((cell.n_providers <= 5).mean() * 100, 1),
        "ge10_share_pct": round((cell.n_providers >= 10).mean() * 100, 1),
        "ge20_share_pct": round((cell.n_providers >= 20).mean() * 100, 1),
        "aspirational_sole_pct": round((cell.loc[cell.aspirational, "n_providers"] == 1).mean() * 100, 1),
        "other_sole_pct": round((cell.loc[~cell.aspirational, "n_providers"] == 1).mean() * 100, 1),
        "km_rows": int(len(acc)),
        "km_percentiles": {f"p{q}": round(float(km.quantile(q / 100)), 1) for q in (25, 50, 75, 90, 95)},
    }
    for t in (50, 100, 150):
        m = acc[km > t]
        pop = m.drop_duplicates("district_code").pop_now.sum()
        f[f"gt{t}km"] = {"cells": int(len(m)), "share_pct": round(len(m) / len(acc) * 100, 1),
                         "districts": int(m.district_code.nunique()), "population_M": round(pop / 1e6, 0)}

    f["by_specialty"] = [
        {"specialty": s, "name": DISPLAY[s], "districts": int(len(g)),
         "sole": int((g.n_providers == 1).sum()),
         "sole_share_pct": round((g.n_providers == 1).mean() * 100, 1),
         "ge15": int((g.n_providers >= 15).sum())}
        for s, g in cell.groupby("specialty")]
    f["by_specialty"].sort(key=lambda r: r["sole_share_pct"])

    # who holds sole-provider slots vs who is in the network
    net = h["Hospital Type"].value_counts(normalize=True)
    sole_slots = hs.merge(sole[["district_code", "specialty"]], on=["district_code", "specialty"])
    held = sole_slots["Hospital Type"].value_counts(normalize=True)
    f["fairness"] = {t: {"network_pct": round(float(net.get(t, 0)) * 100, 1),
                         "sole_pct": round(float(held.get(t, 0)) * 100, 1)}
                     for t in ("Public", "Private(For Profit)", "Private(Not For Profit)", "GOI")}

    # phantom capability: basic-tier facilities listed for tertiary specialties
    basic = hs[hs.basic_tier]
    tert_ids = set(basic.loc[basic.specialty.isin(TERTIARY), "Hospital Id"])
    all_basic = h[h.basic_tier]
    by_state = []
    for st, g in all_basic.groupby("src_state"):
        ids = set(g["Hospital Id"])
        if len(ids) >= 40:
            by_state.append({"state": st.title(), "basic": len(ids),
                             "tertiary_pct": round(len(ids & tert_ids) / len(ids) * 100, 1)})
    by_state.sort(key=lambda r: -r["tertiary_pct"])
    tert_basic_all = all_basic[all_basic["Hospital Id"].isin(tert_ids)]
    top3 = [r["state"].upper() for r in by_state[:3]]
    f["phantom"] = {
        "basic_tier_hospitals": int(len(all_basic)),
        "basic_listing_tertiary": int(len(tert_ids)),
        "basic_listing_tertiary_pct": round(len(tert_ids) / len(all_basic) * 100, 1),
        "top3_states": [s.title() for s in top3],
        "top3_share_pct": round(tert_basic_all.src_state.isin(top3).mean() * 100, 1),
        "by_state": by_state,
        "sole_tertiary_cells_held_by_basic": int(len(
            sole_slots[sole_slots.basic_tier & sole_slots.specialty.isin(TERTIARY)])),
        "sole_tertiary_cells_held_by_basic_outside_bulk_states": int(len(
            sole_slots[sole_slots.basic_tier & sole_slots.specialty.isin(TERTIARY)
                       & ~sole_slots.src_state.isin(BULK_STATES)])),
    }
    return f


def demo_case(cell, acc, feat, h, which) -> dict:
    name, state, spec = which
    row = feat[(feat.district_name_english == name) & (feat.state_name_english == state)]
    assert len(row) == 1, f"demo district {name}, {state} not found uniquely"
    dc = int(row.district_code.iloc[0])
    c = cell[(cell.district_code == dc) & (cell.specialty == spec)]
    a = acc[(acc.district_code == dc) & (acc.specialty == spec)]
    per_spec = (cell[cell.district_code == dc].set_index("specialty").n_providers
                  .sort_values(ascending=False))
    return {
        "district": name, "state": state, "district_code": dc, "specialty": spec,
        "providers": int(c.n_providers.iloc[0]) if len(c) else 0,
        "population_M": round(float(row.pop_now.iloc[0]) / 1e6, 2),
        "aspirational": bool(row.aspirational.iloc[0]),
        "post2011": bool(row.is_post2011_district.fillna(False).iloc[0]),
        "hospitals_in_district": int(h[h.district_code == dc]["Hospital Id"].nunique()),
        "specialties_available": int(len(per_spec)),
        "sole_provider_specialties": int((per_spec == 1).sum()),
        "km_to_alternative": round(float(a.km_to_alternative.iloc[0]), 1) if len(a) else None,
        "nearest_alternative": a.nearest_alternative.iloc[0] if len(a) else None,
        "providers_by_specialty": {DISPLAY[k]: int(v) for k, v in per_spec.items()},
    }


def verify(f: dict, esc: dict, con: dict) -> None:
    """Assert what docs/ quotes. Drift fails the build (risk R-10)."""
    print("\n  verifying documented figures")
    checks = [
        ("occupied cells",          f["cells"],                     11_528, 60),
        ("sole-provider cells",     f["sole_cells"],                 2_202, 30),
        ("sole-provider share %",   f["sole_share_pct"],              19.1, 0.5),
        ("cells <=5 providers %",   f["le5_share_pct"],               50.9, 1.0),
        ("cells >=20 providers %",  f["ge20_share_pct"],              16.8, 0.5),
        ("median km",               f["km_percentiles"]["p50"],       50.7, 1.5),
        (">50 km share %",          f["gt50km"]["share_pct"],         50.7, 2.0),
        ("aspirational sole %",     f["aspirational_sole_pct"],       27.0, 1.0),
        ("elsewhere sole %",        f["other_sole_pct"],              17.9, 1.0),
    ]
    ok = True
    for name, got, want, tol in checks:
        hit = abs(got - want) <= tol
        ok &= hit
        print(f"    {'PASS' if hit else 'FAIL'}  {name:<24} got {got:>10,.1f}   doc {want:>8,.1f}")

    demo = [
        ("demo: sole provider",       esc["providers"] == 1),
        ("demo: aspirational",        esc["aspirational"]),
        ("demo: not a new district",  not esc["post2011"]),
        ("demo: >= 60 km to alt",     (esc["km_to_alternative"] or 0) >= 60),
        ("contrast: >= 50 providers", con["providers"] >= 50),
    ]
    for name, hit in demo:
        ok &= bool(hit)
        print(f"    {'PASS' if hit else 'FAIL'}  {name}")
    if not ok:
        raise SystemExit("\n  Figures or demo case drifted from docs/. Fix the filters or update the docs.")


def build_pseudonymised_registry(h: pd.DataFrame, hs: pd.DataFrame, seed: int = 20260916) -> pd.DataFrame:
    """The registry the pipeline reads -- structure kept, identity removed (EC-1).

    Hospital names and NHA ids are dropped and rows are shuffled before
    pseudonyms are assigned, so HOSP-nnnnn carries no ordering back to the
    source export. The mapping is never written anywhere.
    """
    specs = hs.groupby("Hospital Id").specialty.apply(lambda s: "|".join(sorted(s)))
    reg = (h[["Hospital Id", "district_code", "src_state", "Hospital Type", "basic_tier"]]
             .drop_duplicates("Hospital Id").copy())
    reg["specialties"] = reg["Hospital Id"].map(specs).fillna("")
    reg = reg.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    reg.insert(0, "hospital_ref", [f"HOSP-{i + 1:05d}" for i in range(len(reg))])
    reg = (reg.drop(columns=["Hospital Id"])
              .rename(columns={"src_state": "state", "Hospital Type": "hospital_type"}))
    assert not {"Hospital Id", "Hospital Name"} & set(reg.columns), "identity leaked into registry"
    return reg


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("building reference tables (v2, canonical specialties)\n")

    feat = pd.read_csv(FEAT)
    feat["pop_now"] = 10 ** feat["log_pop_now"]
    asp = pd.read_csv(ASP, comment="#")
    aset = set(pd.to_numeric(asp["district_code"], errors="coerce").dropna().astype(int))
    feat["aspirational"] = feat.district_code.isin(aset)

    h = load_hospitals()
    hs = hospital_specialties(h)
    cell = build_adequacy(hs, aset)
    pts = district_points()
    acc = build_access_distance(cell, pts, feat)

    net = (h.groupby("district_code").agg(hospitals=("Hospital Id", "nunique")).reset_index()
             .merge(feat[["district_code", "district_name_english", "state_name_english",
                          "pop_now", "aspirational"]], on="district_code", how="left"))
    net["hospitals_per_lakh"] = net.hospitals / (net.pop_now / 1e5)
    net = net.merge(cell.groupby("district_code").specialty.nunique().rename("specialties"),
                    on="district_code", how="left")

    f = compute_figures(h, hs, cell, acc, feat)
    esc = demo_case(cell, acc, feat, h, DEMO_ESCALATE)
    con = demo_case(cell, acc, feat, h, DEMO_CONTRAST)
    f["demo_escalate"], f["demo_contrast"] = esc, con

    verify(f, esc, con)

    pd.DataFrame([{"specialty": s, "name": n, "registry_codes": "|".join(c), "tertiary": t}
                  for s, n, c, t in SPECIALTIES]).to_csv(OUT / "specialty_canonical.csv", index=False)
    cell.to_csv(OUT / "district_adequacy.csv", index=False)
    acc.to_csv(OUT / "access_distance.csv", index=False)
    net.to_csv(OUT / "district_network.csv", index=False)
    # Merge, never overwrite: build_state_context.py owns the "official" block, and replacing the whole file
    # dropped it -- re-running this step alone broke metrics.run with KeyError 'official' (F-48). Same JSON
    # settings as that script, so either order of runs writes the same bytes.
    fig_path = OUT / "figures.json"
    existing = json.loads(fig_path.read_text(encoding="utf-8")) if fig_path.exists() else {}
    merged = {**existing, **json.loads(json.dumps(f, default=str))}
    fig_path.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    reg = build_pseudonymised_registry(h, hs)
    reg.to_csv(OUT / "registry_pseudonymised.csv", index=False)
    print(f"  wrote registry_pseudonymised.csv ({len(reg):,} hospitals, no names or NHA ids)")

    print(f"\n  wrote specialty_canonical.csv ({len(SPECIALTIES)} specialties)")
    print(f"  wrote district_adequacy.csv   ({len(cell):,} rows)")
    print(f"  wrote access_distance.csv     ({len(acc):,} rows)")
    print(f"  wrote district_network.csv    ({len(net):,} rows)")
    print("  wrote figures.json")

    print(f"\n  DEMO  {esc['district']}, {esc['state']} - {esc['specialty']}: "
          f"{esc['providers']} provider among {esc['hospitals_in_district']} hospitals, "
          f"{esc['population_M']} M people, {esc['km_to_alternative']} km to {esc['nearest_alternative']}, "
          f"aspirational={esc['aspirational']}")
    print(f"  VS    {con['district']}, {con['state']} - {con['specialty']}: {con['providers']} providers")


if __name__ == "__main__":
    main()
