"""Parse the HBP 2022 package master PDF into a structured table.

    python scripts/parse_hbp_master.py

Reads   rulebooks/HBP_2022_package_master.pdf   (3,801 pages)
Writes  data/reference/hbp_packages.csv
        data/reference/specialty_legend.json    (refreshed)

The PDF is a five-column table: Specialty | Specialty Code | HBP 2022 Procedure
Code | HBP 2.0 Procedure Code | HBP 1.0 Procedure Code, flattened by the text
extractor into a single token stream with names wrapped across lines.

A reference table: it maps HBP 2022 codes to their legacy HBP 1.0 codes and the
registry's specialty codes (the basis of the canonical specialties in
build_reference.py). The pipeline itself does not read it: `hbp_lookup()` in
crew/tools.py reads the package rates and listings built by build_packages.py.
"""
from __future__ import annotations

import json
import re
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import fitz
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "rulebooks/HBP_2022_package_master.pdf"
OUT = ROOT / "data/reference"

SPEC_CODE = re.compile(r"^[A-Z]{2,3}$")                 # BM, ER, SG, ENT
HBP2022   = re.compile(r"^([A-Z]{2,3})(\d{3})([A-Z]?)$")  # BM001A, SU073A
HBP1      = re.compile(r"^([MS])(\d{1,2})(\d{5,6})$")     # S1100001, M700001
NOISE     = {"New", "New Package", "Specialty", "Specialty Code", "HBP", "2022",
             "Procedure", "Code", "2.0", "1.0"}


def tokens() -> list[str]:
    doc = fitz.open(PDF)
    out: list[str] = []
    for i in range(doc.page_count):
        out += [ln.strip() for ln in doc[i].get_text().split("\n") if ln.strip()]
    print(f"  pages {doc.page_count:,} -> {len(out):,} text lines")
    return out


def parse(lines: list[str]) -> pd.DataFrame:
    rows = []
    name_buf: list[str] = []
    cur_name = cur_spec = None

    for i, tok in enumerate(lines):
        if tok in NOISE:
            name_buf.clear()
            continue

        if SPEC_CODE.match(tok):
            # the lines immediately before a specialty code are its name
            if name_buf:
                cur_name = " ".join(name_buf).strip()
                name_buf.clear()
            cur_spec = tok
            continue

        m = HBP2022.match(tok)
        if m:
            # look ahead a few tokens for the HBP 1.0 legacy code
            legacy = None
            for j in range(i + 1, min(i + 4, len(lines))):
                lm = HBP1.match(lines[j])
                if lm:
                    legacy = lines[j]
                    break
                if SPEC_CODE.match(lines[j]) or HBP2022.match(lines[j]):
                    break
            rows.append({
                "package_code":   tok,
                "specialty_code": cur_spec,
                "specialty_name": cur_name,
                "hbp1_code":      legacy,
                "registry_code":  f"{HBP1.match(legacy).group(1)}{int(HBP1.match(legacy).group(2))}"
                                  if legacy else None,
            })
            name_buf.clear()
            continue

        if HBP1.match(tok):
            name_buf.clear()
            continue

        # otherwise it is part of a wrapped specialty name
        if len(tok) > 2 and not tok.isdigit():
            name_buf.append(tok)
            if len(name_buf) > 4:
                name_buf.pop(0)

    df = pd.DataFrame(rows)
    df = df[df.specialty_code.notna()].drop_duplicates("package_code")
    return df.reset_index(drop=True)


def build_legend(df: pd.DataFrame) -> list[dict]:
    """registry code (what the hospital registry stores) -> specialty."""
    by_reg = defaultdict(Counter)
    for r in df.dropna(subset=["registry_code"]).itertuples():
        by_reg[r.registry_code][(r.specialty_code, r.specialty_name)] += 1

    legend = []
    for reg, c in sorted(by_reg.items(), key=lambda kv: -sum(kv[1].values())):
        (spec, name), n = c.most_common(1)[0]
        legend.append({"registry_code": reg, "hbp2022": spec,
                       "name": re.sub(r"\s+", " ", name or "").strip(),
                       "n_packages": int(sum(c.values()))})
    return legend


def main() -> None:
    print("parsing HBP 2022 package master\n")
    df = parse(tokens())
    print(f"  packages parsed          : {len(df):,}")
    print(f"  distinct specialty codes : {df.specialty_code.nunique()}")
    print(f"  with legacy HBP 1.0 code : {df.hbp1_code.notna().sum():,}")

    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "hbp_packages.csv", index=False)

    legend = build_legend(df)
    (OUT / "specialty_legend.json").write_text(json.dumps(legend, indent=1), encoding="utf-8")

    print(f"\n  wrote hbp_packages.csv     ({len(df):,} rows)")
    print(f"  wrote specialty_legend.json ({len(legend)} registry codes)\n")

    print("  registry code -> specialty (top 15 by package count)")
    for e in legend[:15]:
        print(f"    {e['registry_code']:<5} {e['hbp2022']:<4} {e['name'][:44]:<46} {e['n_packages']:>5}")

    top = df.specialty_code.value_counts().head(8)
    print("\n  largest specialties by package count")
    for k, v in top.items():
        nm = df.loc[df.specialty_code == k, "specialty_name"].dropna()
        print(f"    {k:<4} {(nm.iloc[0] if len(nm) else '?')[:40]:<42} {v:>5} packages")


if __name__ == "__main__":
    main()
