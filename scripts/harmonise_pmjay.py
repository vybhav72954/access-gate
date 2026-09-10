#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
harmonise_pmjay.py -- Put the PM-JAY empanelled-hospitals list onto the current
785-district LGD frame (Novel/proxy Tier-1 #2: insured private+public supply,
also rescues our PM-JAY row at district level via the hospital side).

Input  : data/raw/pmjay_hospitals/PMJAY_empanelled_hospitals_2026-07-16.xls
         (no-login Excel export of hospitals.pmjay.gov.in/Search "Registered
         Hospitals" view; re-pull with fetch_pmjay_hospitals.py)
Output : data/processed/crosswalk/pmjay_hospitals_by_district.csv (785 rows)
         data/processed/crosswalk/name_map_pmjay.csv / unmatched_pmjay.csv

Scope / definitions
  - All rows in the export are approved (empanelled or re-empanelled).
  - PM-JAY scope = Empanelment Type NOT in {Only CGHS, Only CAPF} -- central
    government-employee schemes are not population-wide insured supply. "State
    Specific Empanelment" rows ride the same PM-JAY IT platform for converged
    state extensions, so they stay in scope.
  - n_pmjay_private = scope rows typed Private(For Profit) or Private(Not For
    Profit); n_pmjay_public = Public + GOI; untyped rows count in the total only.
  - PSU/NHCP pseudo-states and null-district rows are dropped (logged).
  - District "-NA-" rows are dropped (208 in scope, 0.65%): 198 are legacy
    pre-bifurcation "ANDHRA PRADESH" registrations of Hyderabad hospitals
    (Osmania, Gandhi, Niloufer, Koti/Nampally/Golconda localities) -- 79 of them
    exact name-duplicates of properly-tagged Telangana rows, the rest near-dupes
    or Hyderabad-area; counting them anywhere would double-count Hyderabad.

Known coverage caveat (document, don't fix): the portal covers the PM-JAY
platform only -- West Bengal (runs Swasthya Sathi outside PM-JAY) and the recent
joiners Odisha and Delhi are structurally understated. Their low counts are the
true PM-JAY channel picture, not missing data.

District names are current-vintage (post-2020 districts appear by name), so this
is a name-standardisation job; family apportionment only fires where the portal
still uses a pre-split parent.
"""
import os
import pandas as pd

import build_crosswalk as bx

RAW = os.path.join(bx.RAW, "pmjay_hospitals")
CW = bx.CW
XLS = os.path.join(RAW, "PMJAY_empanelled_hospitals_2026-07-16.xls")

EXCLUDE_EMPANEL = {"Only CGHS", "Only CAPF"}
PSEUDO_STATES = {"PSU", "NHCP"}


def main():
    df = pd.read_excel(XLS, engine="xlrd", header=0)
    df.columns = [str(c).strip() for c in df.columns]
    n0 = len(df)

    dropped_pseudo = df["State"].isin(PSEUDO_STATES).sum()
    dropped_nodist = df["District"].isna().sum()
    dropped_na = (df["District"] == "-NA-").sum()
    df = df[~df["State"].isin(PSEUDO_STATES) & df["District"].notna()
            & (df["District"] != "-NA-")]
    print(f"dropped {dropped_na} district='-NA-' rows (legacy Hyderabad dupes, see docstring)")
    scope = df[~df["Empanelment Type"].isin(EXCLUDE_EMPANEL)].copy()
    print(f"rows: {n0:,} exported -> {len(scope):,} in PM-JAY scope "
          f"(dropped {n0 - dropped_pseudo - dropped_nodist - dropped_na - len(scope):,} "
          f"CGHS/CAPF-only, {dropped_pseudo} pseudo-state, {dropped_nodist} null-district, "
          f"{dropped_na} '-NA-')")

    typ = scope["Hospital Type"].fillna("")
    scope["is_private"] = typ.str.startswith("Private").astype(int)
    scope["is_public"] = typ.isin(["Public", "GOI"]).astype(int)

    agg = (scope.groupby(["State", "District"])
           .agg(n_pmjay_hospitals=("Hospital Id", "size"),
                n_pmjay_private=("is_private", "sum"),
                n_pmjay_public=("is_public", "sum"))
           .reset_index())
    print(f"aggregated: {len(agg)} (state, district) pairs")

    vals = ["n_pmjay_hospitals", "n_pmjay_private", "n_pmjay_public"]
    out = bx.crosswalk_file(agg, "State", "District", vals, source="PMJAY",
                            extensive=True, absent_zero=True,
                            out_csv=os.path.join(CW, "pmjay_hospitals_by_district.csv"))

    for c in vals:
        src, dst = agg[c].sum(), out[c].sum()
        print(f"  [pmjay] {c}: source={src:,.0f}  harmonised={dst:,.0f} "
              f"({dst / src * 100 if src else float('nan'):.2f}%)")
    nz = (out[vals[0]] > 0).sum()
    print(f"pmjay_hospitals_by_district.csv: {len(out)} rows, {nz} districts with >=1 "
          f"empanelled hospital, {int(out['is_apportioned'].sum())} apportioned")

    if bx.NAMEMAP:
        pd.DataFrame(bx.NAMEMAP).to_csv(os.path.join(CW, "name_map_pmjay.csv"), index=False)
    # Always written: skipping the write when nothing was unmatched left a previous run's list in place, which
    # still named districts the aliases had since resolved (F-49).
    unmatched_cols = ["source", "src_state", "src_district", "reason", "best_fuzzy_score", "n_pmjay_hospitals",
                      "n_pmjay_private", "n_pmjay_public"]
    pd.DataFrame(bx.UNMATCHED, columns=unmatched_cols).to_csv(os.path.join(CW, "unmatched_pmjay.csv"), index=False)
    print(f"UNMATCHED: {len(bx.UNMATCHED)} -> unmatched_pmjay.csv")

    blank = out[(out[vals[0]] == 0) & ~out["is_apportioned"]].copy()
    blank["pop2011"] = blank["district_code"].map(lambda c: bx.POP.get(c, 0))
    print(f"review: {len(blank)} districts at zero and not apportioned:")
    if len(blank):
        print(blank.nlargest(15, "pop2011")[["district_name_english",
              "state_name_english", "pop2011"]].to_string(index=False))


if __name__ == "__main__":
    main()
