"""Official PM-JAY statistics -> state context, specialty volumes, national figures.

    python scripts/fetch_ogd.py              # once; needs DATA_GOV_IN_API_KEY
    python scripts/build_state_context.py    # no network

Writes  data/reference/state_context.csv     one row per state/UT
        data/reference/specialty_volume.csv  official admissions by canonical specialty
        data/reference/figures.json          adds the "official" block

Every input is a table the Ministry of Health tabled in Parliament
(data/external/ogd/_manifest.json). Like build_reference.py, this script checks
itself: the sources are reconciled against one another and the run fails if
they stop agreeing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OGD = ROOT / "data/external/ogd"
REF = ROOT / "data/reference"
REG = ROOT / "data/registry/PMJAY_empanelled_hospitals_2026-07-16.xls"

FRAUD_BASE_RATE = 0.0018     # PIB PRID 1847423: confirmed fraud share of authorised admissions
FLAG_FPR = 0.01              # the false-positive rate the BRD's base-rate argument uses
FLAG_RECALL = 0.90           # the recall that argument assumes (~86% of flags innocent)
FY = "2024-25"
YEARS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

DNH_DD = "DADRA AND NAGAR HAVELI AND DAMAN AND DIU"
ALIASES = {"NCT OF DELHI": "DELHI", "DADRA AND NAGAR HAVELI": DNH_DD, "DAMAN AND DIU": DNH_DD}
NOT_STATES = {"TOTAL", "PSU", "GRAND TOTAL"}

# Official specialty labels -> canonical specialties (data/reference/specialty_canonical.csv).
# "Infectious Diseases" has no canonical counterpart (HBP 2022 code ID is unscored).
SPECIALTY_2024 = {
    "General Medicine": "general_medicine", "Cardiology": "cardiology", "Medical Oncology": "medical_oncology",
    "Orthopedics": "orthopaedics", "General Surgery": "general_surgery", "Urology": "urology",
    "Radiation Oncology": "radiation_oncology", "Cardiothoracic Vascular Surgery": "ctvs",
    "Obstetrics and Gynaecology": "obgyn", "Ophthalmology": "ophthalmology", "Neo - natal Care": "neonatal",
    "Neurosurgery": "neurosurgery", "Surgical Oncology": "surgical_oncology",
    "Pediatric Medical Management": "paediatric_medicine", "ENT": "ent", "Pediatric Surgery": "paediatric_surgery",
    "Polytrauma": "polytrauma", "Emergency Room Packages": "emergency_room",
    "Plastic and Reconstructive Surgery": "plastic_surgery",
}
# Four canonical specialties the 2024 answer omits; the 2021 answer lists them.
SPECIALTY_2021_ONLY = {
    "Burns Management": "burns", "Interventional Neuroradiology": "interventional_neuroradiology",
    "Mental Disorders Packages": "mental_disorders", "Oral and Maxillofacial Surgery": "oral_maxillofacial",
}
# Specialties both answers report under the same scope, used to scale 2021 counts to 2024.
BRIDGE_2021 = {
    "Cardiology": "cardiology", "Cardio-thoracic Vascular Surgery": "ctvs", "General Surgery": "general_surgery",
    "Neo-natal care": "neonatal", "Neurosurgery": "neurosurgery", "Obstetrics and Gynaecology": "obgyn",
    "Ophthalmology": "ophthalmology", "Orthopaedics": "orthopaedics", "Otorhinolaryngology (ENT)": "ent",
    "Paediatric medical management": "paediatric_medicine", "Paediatric surgery": "paediatric_surgery",
    "Plastic and Reconstructive Surgery": "plastic_surgery", "Polytrauma": "polytrauma", "Urology": "urology",
    "Emergency Room Packages": "emergency_room",
}


def canon(name: str) -> str:
    s = re.sub(r"[^A-Z ]", " ", str(name).upper().replace("&", " AND "))
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def ogd(slug: str) -> pd.DataFrame:
    return pd.read_csv(OGD / f"{slug}.csv", keep_default_na=False)


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "").replace({"NA": None, "": None}), errors="coerce")


def by_state(slug: str, state_col: str = "State/UT") -> pd.DataFrame:
    """Numeric columns summed per canonical state (merges DNH and Daman & Diu), totals dropped."""
    df = ogd(slug)
    df["state"] = df[state_col].map(canon)
    df = df[~df.state.isin(NOT_STATES)]
    vals = df.drop(columns=[c for c in df.columns if c in (state_col, "Sl. No.", "state")]).apply(num)
    vals["state"] = df.state
    return vals.groupby("state").sum(min_count=1)


# ── registry, by state ─────────────────────────────────────────────────────

def registry_by_state() -> pd.DataFrame:
    raw = pd.read_excel(REG)
    raw = raw[raw["Empanelment Type"].astype(str).str.contains("PMJAY", na=False)]
    counts = raw.groupby(raw.State.map(canon)).size().rename("registry_hospitals_raw")
    scoped = pd.read_csv(REF / "registry_pseudonymised.csv", keep_default_na=False)
    scoped["state"] = scoped.state.map(canon)
    g = scoped.groupby("state")
    out = pd.concat([
        counts,
        g.size().rename("registry_hospitals_in_scope"),
        g.hospital_type.apply(lambda t: t.isin(["Public", "GOI"]).mean() * 100).round(1).rename("registry_public_pct"),
    ], axis=1)
    return out.drop(index=[i for i in out.index if i in NOT_STATES], errors="ignore")


# ── build ──────────────────────────────────────────────────────────────────

def build() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    adm = by_state("admissions_2018_2025")                      # year-wise counts
    amt = by_state("admissions_amount_2018_2025")               # year-wise Rs crore
    cnt = by_state("admissions_count_amount_2021_2024")         # a separate answer, for reconciliation
    auth = by_state("admissions_authorised_2019_2025")          # a cumulative answer that does NOT reconcile
    dep = by_state("hospitals_deempanelled_by_ownership")
    emp = by_state("hospitals_empanelled_by_ownership")
    opt = by_state("hospitals_opted_out")
    naf = by_state("claims_nonadmissible_abuse_private")
    mob = by_state("beneficiaries_single_mobile", "State / UT")
    off = by_state("hospitals_empanelled_2025_03")
    mh = by_state("admissions_mental_health")

    # ── reconciliation: two answers tabled months apart must agree ─────────
    for y in ("2021-22", "2022-23", "2023-24"):
        a, b = adm[y].dropna(), cnt[f"{y} - Count"].reindex(adm[y].dropna().index)
        assert (a == b).all(), f"admission counts for {y} disagree between answers"
        da = (amt[y] - cnt[f"{y} - Amount"]).abs().max()
        assert da <= 0.15, f"amounts for {y} disagree by up to {da} crore - unit or parse error"
    published_total = int(num(ogd("hospitals_empanelled_2025_03").iloc[:, 2]).iloc[-1])
    col_off = off.columns[0]
    assert int(off[col_off].sum()) == published_total, "state rows do not sum to the published total"

    s = pd.DataFrame(index=sorted(set(adm.index) | set(off.index)))
    s.index.name = "state"
    s["admissions_2024_25"] = adm[FY]
    s["amount_cr_2024_25"] = amt[FY]
    s["avg_claim_rs_2024_25"] = (amt[FY] * 1e7 / adm[FY]).round(0)
    s["admissions_2018_25"] = adm[YEARS].sum(axis=1, min_count=1)
    s["amount_cr_2018_25"] = amt[YEARS].sum(axis=1, min_count=1).round(1)
    s["expected_confirmed_fraud_2024_25"] = (adm[FY] * FRAUD_BASE_RATE).round(0)
    s["expected_innocent_flags_2024_25"] = (adm[FY] * (1 - FRAUD_BASE_RATE) * FLAG_FPR).round(0)
    s["nonadmissible_private_cr"] = (naf.iloc[:, 0] / 100).round(2)            # published in Rs lakh
    s["nonadmissible_share_of_amount_pct"] = (s.nonadmissible_private_cr / s.amount_cr_2018_25 * 100).round(3)
    pub = [c for c in dep.columns if c.endswith("Public")]
    pri = [c for c in dep.columns if c.endswith("Private")]
    s["deempanelled_public_2018_25"] = dep[pub].sum(axis=1)
    s["deempanelled_private_2018_25"] = dep[pri].sum(axis=1)
    s["empanelled_public_2018_25"] = emp[[c for c in emp.columns if c.endswith("Public")]].sum(axis=1)
    s["empanelled_private_2018_25"] = emp[[c for c in emp.columns if c.endswith("Private")]].sum(axis=1)
    s["opted_out_2019_25"] = opt.sum(axis=1, min_count=1)
    s["single_mobile_beneficiaries_2018_21"] = mob.iloc[:, 0]
    s["mental_health_admissions_2022"] = mh.iloc[:, 0]
    s["official_hospitals_2025_03"] = off[col_off]
    s = s.join(registry_by_state(), how="left")
    s["registry_minus_official"] = s.registry_hospitals_raw - s.official_hospitals_2025_03
    s.insert(0, "state_name", [i.title().replace(" And ", " and ") for i in s.index])

    # ── specialty volume ───────────────────────────────────────────────────
    sp24 = ogd("admissions_by_specialty_2024")
    sp24["count"] = num(sp24.iloc[:, 2]) * 1e5                                  # published in lakh
    sp24["amount_cr"] = num(sp24.iloc[:, 3])
    sp24["specialty"] = sp24["Specialty"].map(SPECIALTY_2024)
    sp21 = ogd("admissions_by_specialty_2021")
    sp21["count"] = num(sp21.iloc[:, 1])
    sp21["amount_cr"] = num(sp21.iloc[:, 2])
    bridge = sp21[sp21.Specialty.isin(BRIDGE_2021)]
    scale = sp24.set_index("specialty").loc[bridge.Specialty.map(BRIDGE_2021), "count"].sum() / bridge["count"].sum()
    rows = [(r.specialty, r.Specialty, int(round(r["count"])), float(r.amount_cr), "2024-06-30 answer")
            for _, r in sp24.iterrows() if isinstance(r.specialty, str)]
    rows += [(SPECIALTY_2021_ONLY[r.Specialty], r.Specialty, int(round(r["count"] * scale)),
              round(float(r.amount_cr) * scale, 1), f"2021-02-02 answer x {scale:.2f}")
             for _, r in sp21.iterrows() if r.Specialty in SPECIALTY_2021_ONLY]
    spec = pd.DataFrame(rows, columns=["specialty", "official_label", "admissions", "amount_cr", "basis"])
    canonical = set(pd.read_csv(REF / "specialty_canonical.csv", keep_default_na=False).specialty)
    assert set(spec.specialty) <= canonical, set(spec.specialty) - canonical
    spec["share_pct"] = (spec.admissions / spec.admissions.sum() * 100).round(2)
    spec["avg_claim_rs"] = (spec.amount_cr * 1e7 / spec.admissions).round(0)
    spec = spec.sort_values("admissions", ascending=False)
    missing = sorted(canonical - set(spec.specialty))          # paediatric_cancer: folded into oncology

    # ── national figures ───────────────────────────────────────────────────
    n_fy, amt_fy = int(adm[FY].sum()), float(amt[FY].sum())
    fraud = n_fy * FRAUD_BASE_RATE
    innocent = n_fy * (1 - FRAUD_BASE_RATE) * FLAG_FPR
    caught = fraud * FLAG_RECALL
    dep_pub, dep_pri = int(s.deempanelled_public_2018_25.sum()), int(s.deempanelled_private_2018_25.sum())
    dep_cells = dep.stack()
    peak = dep_cells.idxmax()
    reported = s.dropna(subset=["nonadmissible_private_cr"])
    big = reported[reported.amount_cr_2018_25 >= 1000]
    top3 = reported.nonadmissible_private_cr.sort_values(ascending=False).head(3)
    auth_ratio = float(auth.iloc[:, 0].sum() / adm[YEARS[1:]].sum(axis=1).sum())
    up = num(ogd("admissions_district_up_2021_22").set_index("District")["No. of Authorized Hospital Admissions"])
    gj = num(ogd("admissions_district_gujarat_2023").set_index("District")["Number of Authorized Hospital Admissions"])

    official = {
        "source": "data/external/ogd/_manifest.json (Rajya Sabha answers via data.gov.in)",
        "fy": FY,
        "admissions": n_fy,
        "amount_cr": round(amt_fy, 1),
        "avg_claim_rs": round(amt_fy * 1e7 / n_fy),
        "admissions_per_day": round(n_fy / 365),
        "expected_confirmed_fraud": round(fraud),
        "expected_confirmed_fraud_per_day": round(fraud / 365),
        "expected_innocent_flags_at_1pct_fpr": round(innocent),
        "expected_flags_per_day": round((caught + innocent) / 365),
        "innocent_share_of_flags_pct": round(innocent / (caught + innocent) * 100, 1),
        "hospitals_official_2025_03": published_total,
        "hospitals_registry_pmjay_scope": int(s.registry_hospitals_raw.sum()),
        "registry_vs_official_pct": round((s.registry_hospitals_raw.sum() / published_total - 1) * 100, 1),
        "states_exact_match": int((s.registry_minus_official == 0).sum()),
        "states_within_10": int((s.registry_minus_official.abs() <= 10).sum()),
        "states_compared": int(s.official_hospitals_2025_03.notna().sum()),
        "largest_registry_excess": {k.title(): int(v) for k, v in
                                    s.registry_minus_official.sort_values(ascending=False).head(3).items()},
        "deempanelled_2018_25": {"public": dep_pub, "private": dep_pri,
                                 "public_share_pct": round(dep_pub / (dep_pub + dep_pri) * 100, 1)},
        "deempanelled_peak_state_year": {
            "state": peak[0].title(), "year": peak[1].split(" - ")[0], "hospitals": int(dep_cells.max())},
        "opted_out_2019_25": int(s.opted_out_2019_25.sum()),
        "nonadmissible_private_cr_as_on_2025_01_14": round(float(reported.nonadmissible_private_cr.sum()), 1),
        "nonadmissible_states_reporting": int(len(reported)),
        "nonadmissible_top3": {k.title(): round(float(v), 1) for k, v in top3.items()},
        "nonadmissible_top3_share_pct": round(float(top3.sum() / reported.nonadmissible_private_cr.sum() * 100), 1),
        "nonadmissible_share_range_pct_states_over_1000cr": {
            "min_state": big.nonadmissible_share_of_amount_pct.idxmin().title(),
            "min": float(big.nonadmissible_share_of_amount_pct.min()),
            "max_state": big.nonadmissible_share_of_amount_pct.idxmax().title(),
            "max": float(big.nonadmissible_share_of_amount_pct.max())},
        "single_mobile_beneficiaries_2018_21": int(s.single_mobile_beneficiaries_2018_21.sum()),
        "authorised_series_vs_yearwise_ratio": round(auth_ratio, 3),
        "specialty_missing_from_official_tables": missing,
        "specialty_2021_scale": round(float(scale), 3),
        "demo_districts": {
            "bahraich_admissions_2021_22": int(up["Bahraich"]),
            "gonda_admissions_2021_22": int(up["Gonda"]),
            "up_district_admissions_2021_22_median": int(up.median()),
            "ahmedabad_admissions_as_of_2023_08": int(gj["Ahmadabad"]),
            "gujarat_outside_state_admissions_as_of_2023_08": int(gj["Outside State"]),
        },
    }
    return s.reset_index(), spec, official


def main() -> None:
    s, spec, official = build()
    s.to_csv(REF / "state_context.csv", index=False)
    spec.to_csv(REF / "specialty_volume.csv", index=False)
    fig_path = REF / "figures.json"
    fig = json.loads(fig_path.read_text(encoding="utf-8"))
    fig["official"] = official
    fig_path.write_text(json.dumps(fig, indent=1, ensure_ascii=False), encoding="utf-8")

    o = official
    print(f"  state_context.csv          {len(s)} states/UTs")
    print(f"  specialty_volume.csv       {len(spec)} specialties (missing: {', '.join(o['specialty_missing_from_official_tables'])})")
    print(f"  admissions {o['fy']}         {o['admissions']:,}  (Rs {o['amount_cr']:,} crore, avg Rs {o['avg_claim_rs']:,})")
    print(f"  at 0.18% / 1% FPR          {o['expected_confirmed_fraud']:,} frauds/yr, {o['expected_flags_per_day']:,} flags/day, "
          f"{o['innocent_share_of_flags_pct']}% innocent")
    print(f"  registry vs official       {o['hospitals_registry_pmjay_scope']:,} vs {o['hospitals_official_2025_03']:,} "
          f"({o['registry_vs_official_pct']:+}%); {o['states_exact_match']}/{o['states_compared']} states exact, "
          f"{o['states_within_10']} within 10")
    d = o["deempanelled_2018_25"]
    print(f"  de-empanelled 2018-25      public {d['public']}, private {d['private']} ({d['public_share_pct']}% public)")
    r = o["nonadmissible_share_range_pct_states_over_1000cr"]
    print(f"  non-admissible (private)   Rs {o['nonadmissible_private_cr_as_on_2025_01_14']:,} crore, "
          f"{o['nonadmissible_states_reporting']} states; share of amount {r['min']}% ({r['min_state']}) "
          f"to {r['max']}% ({r['max_state']})")

    # ── self-verification (figures quoted in docs must not drift) ─────────
    assert o["hospitals_official_2025_03"] == 30_957
    assert o["states_exact_match"] >= 21 and o["states_within_10"] >= 30 and abs(o["registry_vs_official_pct"]) < 1.0
    assert 0.80 <= o["innocent_share_of_flags_pct"] / 100 <= 0.90, "the ~86% innocent-flag argument no longer holds"
    assert o["demo_districts"]["bahraich_admissions_2021_22"] == 8_990
    assert o["demo_districts"]["ahmedabad_admissions_as_of_2023_08"] == 292_857
    print("  verified                   reconciliation + 5 figure checks")


if __name__ == "__main__":
    main()
