"""Build the package tables the triggers read, from the published package master.

    python scripts/build_packages.py

Reads   data/packages/raw_*.json                Punjab SHA's published HBP 2.0 package master, one file per specialty
Writes  data/reference/hbp_package_rates.csv    1,602 packages: rate, mandatory documents (first listing's flags)
        data/reference/hbp_package_listings.csv every specialty a package is listed under, with that listing's
                                                government-reserved flag
        data/reference/daycare_candidates.csv   packages where a same-day discharge is plausibly legitimate

473 packages are listed under two to four specialties (a paediatric package under both General Medicine and
Paediatric Medical Management, say), and 52 of them are government-reserved under one listing and not another.
The rates table keeps one row per package; the listings table keeps them all, because a hospital may bill a
package through any specialty it lists it under (F-35).

Both tables existed before this script did, with no record of how they were made (F-33). The rates table is
rebuilt byte for byte. The day-care list is rebuilt with its name rule corrected: the first version matched
substrings anywhere in a package's text, so "opd" found COPD and COPDAC, "follow" found "the following conditions",
and "dressing" found the follow-up dressings INCLUDED in skin-grafting packages. Twenty-four inpatient packages --
seventeen burns surgeries of Rs 30,000-80,000, acute exacerbation of COPD, respiratory failure, two neonatal
intensive-care packages -- were marked day-care, so trigger 2 could never fire on the burns surgeries and trigger 3
never on COPD or respiratory failure. The list is still a CANDIDATE list for clinical review
(docs/04-DATA-DICTIONARY.md §9a); the rule is now one a reviewer can read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/packages"
OUT = ROOT / "data/reference"

COLUMNS = {"specialityid": "specialty_code", "specialityname": "specialty_name", "proc_id": "package_code",
           "proc_name": "package_name", "pkg_amt": "package_amount_rs", "preinvestigations": "pre_investigations",
           "postinvestigations": "post_investigations", "govtreserved": "govt_reserved",
           "refferalbasis": "referral_basis"}

LOW_VALUE_RS = 3_000          # at or below this, not an inpatient surgery (excluding per-day packages priced at 0)
# Said of the package ITSELF: its name before any ':' or ';', where inclusions and eligibility conditions begin.
SAME_DAY = re.compile(r"\b(?:follow[- ]?up|not requiring admission|(?:haemo|hemo|peritoneal )?dialysis|transfusions?|"
                      r"injections?|dressings?|day[- ]?care|OPD)\b", re.I)

# The packages the first, substring-based name rule marked day-care that are inpatient care (F-33).
REVIEWED_INPATIENT = {"BM001B", "BM001C", "BM001D", "BM002B", "BM002C", "BM002D", "BM003B", "BM003C", "BM003D",
                      "BM004A", "BM004B", "BM004C", "BM004D", "BM005A", "BM005B", "BM006A", "BM006B",
                      "MG029A", "MG040C", "MN003A", "MN004A", "MO003B", "MO066A", "SV028A"}


def raw_listings() -> pd.DataFrame:
    """Every row of every specialty file, in file order."""
    files = sorted(RAW.glob("raw_*.json"))
    assert files, f"no package master files in {RAW}"
    rows = []
    for f in files:
        rows += json.loads(f.read_text(encoding="utf-8"))
    return pd.DataFrame(rows).rename(columns=COLUMNS)[list(COLUMNS.values())]


def rates(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per package code. A package listed under two specialties' files keeps its first listing (ER's
    cross-listed emergency packages sort first), ordered by specialty and code."""
    return raw.drop_duplicates("package_code").sort_values(["specialty_code", "package_code"], kind="stable")


def listings(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per (package, specialty listing). Rates and required documents never differ between a package's
    listings (asserted); the government-reserved flag does, for 52 packages."""
    for col in ("package_amount_rs", "pre_investigations", "post_investigations"):
        varies = raw.groupby("package_code")[col].nunique()
        assert (varies == 1).all(), f"{col} differs between listings of {list(varies[varies > 1].index[:5])}"
    # referral_basis is published only for reserved listings: "Yes" (9) lets a private hospital bill the package on
    # a government referral; "No" (180) does not -- a referral letter cannot clear those (F-51).
    out = raw[["package_code", "specialty_code", "govt_reserved", "referral_basis"]].drop_duplicates(
        ["package_code", "specialty_code"])
    home = out.groupby("package_code").apply(lambda g: (g.specialty_code == g.name[:2]).any(), include_groups=False)
    assert home.all(), f"packages not listed under their own code's specialty: {list(home[~home].index[:5])}"
    return out.sort_values(["package_code", "specialty_code"], kind="stable")


def name_head(name: str) -> str:
    return re.split(r"[;:]", " ".join(str(name).split()), maxsplit=1)[0]


def daycare(r: pd.DataFrame) -> pd.DataFrame:
    amount = pd.to_numeric(r.package_amount_rs, errors="coerce")
    groups = [
        (r.specialty_code == "ER", "ER: <12hr stay by definition"),                 # NHA: care needing < 12 hrs
        (r.package_name.map(lambda n: bool(SAME_DAY.search(name_head(n)))), "name implies same-day care"),
        ((amount > 0) & (amount <= LOW_VALUE_RS), "low value, not inpatient surgery"),
    ]
    out = pd.concat([r.loc[mask, ["package_code", "specialty_code", "package_name", "package_amount_rs"]]
                     .assign(daycare_reason=reason) for mask, reason in groups])
    return out.drop_duplicates("package_code")


def main() -> None:
    raw = raw_listings()
    r = rates(raw)
    r.to_csv(OUT / "hbp_package_rates.csv", index=False, lineterminator="\r\n")
    print(f"  hbp_package_rates.csv      {len(r):,} packages")
    ls = listings(raw)
    ls.to_csv(OUT / "hbp_package_listings.csv", index=False, lineterminator="\r\n")
    multi = ls.groupby("package_code").size()
    mixed = ls.groupby("package_code").govt_reserved.nunique()
    print(f"  hbp_package_listings.csv   {len(ls):,} listings; {int((multi > 1).sum())} packages under several "
          f"specialties, {int((mixed > 1).sum())} reserved under some listings only")

    d = daycare(r)
    assert not set(d.package_code) & REVIEWED_INPATIENT, "a reviewed inpatient package was marked day-care again"
    burns_major = r[(r.specialty_code == "BM") & (pd.to_numeric(r.package_amount_rs) >= 20_000)]
    assert not set(burns_major.package_code) & set(d.package_code), "a major burns surgery was marked day-care"
    d.to_csv(OUT / "daycare_candidates.csv", index=False, lineterminator="\r\n")
    print(f"  daycare_candidates.csv     {len(d):,} packages "
          f"({', '.join(f'{n} {reason}' for reason, n in d.daycare_reason.value_counts().items())})")


if __name__ == "__main__":
    main()
