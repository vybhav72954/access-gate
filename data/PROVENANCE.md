# Data Provenance

Every file in this tree, where it came from, and whether it is real or generated.
**No file here was created for this project except those marked *derived* or *generated*.**

---

## Upstream source

The registry, crosswalk, district frame and geometry were assembled for the
**Trilytics × Sun Pharma case** (district-level pharmaceutical market attractiveness),
a prior competition entry. That was a case-competition entry. Reuse is disclosed in the problem brief — see
[risk R-15](../docs/06-RISK-REGISTER.md).

What we inherit is the expensive, boring part: **harmonising Indian district names across sources of
different vintages onto the current 785-district LGD frame.** That is weeks of work and it is done.

---

## `data/registry/`

| File | Source | Status |
|---|---|---|
| `PMJAY_empanelled_hospitals_2026-07-16.xls` | hospitals.pmjay.gov.in — Registered Hospitals view, no-login all-India Excel export, pulled 16 July 2026 | **Real** |
| `_manifest.json` | Pull metadata: 35,286 rows, status and empanelment-type breakdown | **Real** |
| `fetch_pmjay_hospitals.py` | The fetch script. Replicates the page's `fn_openExcel` POST | **Real** |

35,286 rows. All rows are approved: 34,523 Approved for Empanelment + 763 for Re-Empanelment.
District names are current-vintage.

**Re-pull:** `python data/registry/fetch_pmjay_hospitals.py` (server-side generation takes 2–4 min).

---

## `data/external/ogd/` — official statistics

**21 tables the Ministry of Health tabled in Parliament**, published by the Rajya Sabha Secretariat on
data.gov.in and pulled through the OGD API on 16 September 2026. **Real.**

**Re-pull:** `python scripts/fetch_ogd.py` (needs `DATA_GOV_IN_API_KEY` in `.env`; `--refresh` to re-fetch).
Each resource is saved raw (`<slug>.json`, with the API's field metadata) and as CSV with the published
column labels. `_manifest.json` records the resource id, catalogue dates, row count, SHA-256 of the CSV and
the request URL **with the API key redacted** — the script asserts the key never reaches the manifest.

| Group | Slugs | Used for |
|---|---|---|
| Enforcement | `claims_nonadmissible_abuse_private`, `hospitals_deempanelled_by_ownership`, `hospitals_empanelled_by_ownership`, `hospitals_opted_out`, `beneficiaries_single_mobile` | Whom enforcement has fallen on; scale of abuse findings by state |
| Volumes | `admissions_2018_2025`, `admissions_amount_2018_2025`, `admissions_count_amount_2021_2024`, `admissions_authorised_2019_2025`, `claims_submitted_paid_pending`, `beneficiaries_treated_since_inception`, `treatment_expenditure_since_inception`, `funds_released_2019_2025` | Claims per state and year → the corpus's state weights and per-year scaling |
| Specialty | `admissions_by_specialty_2024`, `admissions_by_specialty_2021`, `admissions_mental_health` | The corpus's specialty mix |
| Network | `hospitals_empanelled_2025_03`, `hospitals_empanelled_ne_wb_2024` | Validates the registry snapshot |
| Demo districts | `admissions_district_up_2021_22`, `admissions_district_gujarat_2023`, `hospitals_district_gujarat_2022` | Bahraich and Ahmedabad volumes; calibrates the corpus's district allocation |

**Two checks this data made possible:**

- **The registry is validated.** Our export has 31,196 PMJAY-scope hospitals; the Ministry
  reported 30,957 as on 1 March 2025 (+0.8%). 21 of 36 states match
  exactly and 30 are within 10. The export carries no dates, so read it as the network of roughly early
  2025 (data defect D-8).
- **The volume tables reconcile.** Year-wise admission counts in two answers tabled months apart agree exactly
  for 2021-22 to 2023-24, and amounts to within Rs 0.1 crore. A third, cumulative series does not
  reconcile (D-7) and is not used. `build_state_context.py` asserts both.

---

## `data/crosswalk/`

| File | Contents | Status |
|---|---|---|
| `name_map_pmjay.csv` | 718 source `(state, district)` pairs → LGD `district_code`, with match method and score | Real, derived |
| `unmatched_pmjay.csv` | Source districts the crosswalk could not place: none, once the documented drops apply (208 legacy `-NA-` Hyderabad rows, pseudo-states). Header only; a stale earlier list was replaced (F-49) | Real, derived |
| `pmjay_hospitals_by_district.csv` | Harmonised counts on the 785-district frame | Real, derived |
| `README.md` | The crosswalk methodology — why name-standardisation, not a code join | — |

**Placement rate: 99.3%** (35,042 of 35,286). `build_reference.py` asserts this stays above 99%.

---

## `data/district/`

| File | Contents | Status |
|---|---|---|
| `features_by_district.csv` | **785 × 93.** Population, disease prevalence, OPD/IPD utilisation, cancer incidence, health infrastructure, financial access | Real, harmonised |
| `niti_aspirational_districts.csv` | The official NITI Aayog 112. Comment-prefixed — read with `comment="#"` | **Real** |
| `district_master.csv` | LGD current-district master | **Real** |
| `coverage_report.csv` | Per-feature observed / inherited / imputed counts | Real, derived |
| `variable_registry.csv` | Definition and rationale for every feature | — |

Underlying sources: NFHS-5 (2019–21), HMIS (2018–20), NCRP cancer registry, SECC 2011, Census
migration, GHS-POP, RBI banking statistics, NMC seat matrices, OpenStreetMap POIs, PM-JAY,
Jan Aushadhi.

**Fields this project uses:** `district_code`, `log_pop_now` (population = `10 ** x`),
`oop_delivery_rs` (cost model), `is_post2011_district` (vintage flag — data defect D-2),
`health_insurance_pct`, disease prevalence for incidence.

---

## `data/geo/`

| File | Contents | Status |
|---|---|---|
| `district_boundaries.geojson` | 734 district MultiPolygons, keyed to `district_code` | **Real** (geoBoundaries IND ADM2) |
| `district_geo_crosswalk.csv` | Boundary `shapeID` → LGD `district_code`, with resolve method | Real, derived |

Used only to compute a **representative point** per district — a point guaranteed to fall inside
irregular shapes, unlike a centroid.

---

## `data/reference/` — generated

**Rebuild:** `python scripts/build_reference.py`

| File | Rows | Contents |
|---|---|---|
| `district_adequacy.csv` | 11,528 | One row per occupied district × specialty cell: providers, private/public split, rare-specialty and aspirational flags |
| `access_distance.csv` | 2,185 | Per sole-provider cell: km to nearest other district with that specialty, plus population |
| `district_network.csv` | 687 | Per district: hospitals, specialties available, hospitals per lakh |
| `hbp_packages.csv` | 3,058 | Package code → specialty → legacy HBP 1.0 code, parsed from the master PDF |
| **`hbp_package_rates.csv`** | **1,602** | **Package code → rate in ₹, pre-investigations, post-investigations, govt-reserved flag. Backs `hbp_lookup`. Rebuild: `python scripts/build_packages.py`** |
| `hbp_package_listings.csv` | 2,196 | Every specialty each package is listed under, with that listing's government-reserved flag and referral basis (473 packages have several; 9 reserved packages allow referral). Rebuild: `python scripts/build_packages.py` |
| `daycare_candidates.csv` | 95 | Packages where same-day discharge is plausibly legitimate — **candidates, needs clinical review**. Rebuild: `python scripts/build_packages.py` (with `hbp_package_rates.csv`, from `data/packages/raw_*.json`) |
| `district_points.csv` | 734 | Representative point + population per district. **Gives distance between ANY two districts** — unblocks triggers T5 and T6 |
| **`state_context.csv`** | **36** | **Per state: admissions and amount 2018-25, expected frauds and flags, de-empanelments by ownership, non-admissible claims, opt-outs, official vs registry hospital counts.** Rebuild: `python scripts/build_state_context.py` |
| **`specialty_volume.csv`** | **23** | **Official admissions and amount per canonical specialty — the corpus's specialty mix** |
| `triggers.json` | 10 | 7 parsed from the guidebook's Annexure 2 with channels and checklists, plus 3 derived from published data. **Backs `trigger_guidance`** |

> **Read the specialty tables with `keep_default_na=False`.** `NA` is Paediatric Cancer; pandas
> converts it to `NaN` and silently drops 50 cells. See data defect D-5.
| `specialty_legend.json` | 22 | Registry code → HBP 2022 code → specialty name, with package counts |

`hbp_packages.csv` and the refreshed legend are produced by `scripts/parse_hbp_master.py`, which
parses the 3,801-page HBP 2022 master PDF. Without it the desk-audit agent cannot tell which
specialty a billed package belongs to, and trigger 2 — *zero LOS on a **major surgical** package* —
cannot be evaluated at all.

### Filters applied — must be identical everywhere

```python
EMPANELMENT_SCOPE = "PMJAY"     # excludes Only CGHS, State Specific, CAPF
EXCLUDE_STATES = {"DELHI", "NCT OF DELHI", "ODISHA", "WEST BENGAL"}
```

No official admission table through 2024-25 lists any of the three: they were not operating PM-JAY in
the period our volumes cover, and their networks were still being built in 2025 (Delhi 73, Odisha 12,
West Bengal 43 hospitals as on 1 March 2025). Left in, they read as catastrophic network gaps (finding C-01). After filters: **30,858 hospitals**.

### Self-verification

`build_reference.py` **asserts** nine documented figures and the demo case, and fails the run if any drifts. This is
the control for [risk R-10](../docs/06-RISK-REGISTER.md) — the report and the code cannot disagree
without someone noticing.

```
PASS  occupied cells              11,528
PASS  sole-provider cells           2,202
PASS  sole-provider share %          19.1
PASS  cells <=5 providers %          50.9
PASS  cells >=20 providers %         16.8
PASS  median km                      50.7
PASS  >50 km share %                 50.7
PASS  aspirational sole-provider %   27.0
PASS  elsewhere sole-provider %      17.9
PASS  demo: Bahraich has exactly one real cardiology provider; Ahmedabad has 101
```

Six specialties are excluded from adequacy scoring for existing in fewer than 50 districts
nationally: `CP`, `OC`, `M10`, `OT`, `TG`, `JR`.

---

## `data/packages/` — raw capture

23 JSON responses (~1 MB) from Punjab SHA's public package-master endpoint,
`sha.punjab.gov.in/shapb/publicPages/packageMasterFetch.php`, one per specialty. Kept raw so the
consolidation is reproducible and auditable.

**This is HBP 2.0 as implemented by Punjab under AB PM-JAY MMSBY.** Rates are Punjab's; other states
may vary, and HBP 2022 introduced tiered pricing by city tier. State this when quoting a rate.

---

## `rulebooks/` — the corpus the agents read

| File | Source | Pages |
|---|---|---|
| `NHA_Empanelment_Guidelines_2021.pdf` | [nitiforstates.gov.in](https://www.nitiforstates.gov.in/public-assets/Policy/policy_files/GNC509Q000048.pdf) | 46 |
| `NHA_AntiFraud_Guidebook.pdf` | [cdnbbsr.s3waas.gov.in](https://cdnbbsr.s3waas.gov.in/s3169779d3852b32ce8b1a1724dbf5217d/uploads/2024/09/20240924831436164.pdf) | 164 |
| `NHA_AntiFraud_Guidebook.txt` | Text extraction of the above, for programmatic lookup | — |
| `NHA_Field_Investigation_Manual.pdf` | [sha.kerala.gov.in](https://sha.kerala.gov.in/wp-content/uploads/2026/03/NHA_Field-Investigation-and-Medical-Audit-Manual_April-2020.pdf) | — |
| `HBP_2022_package_master.pdf` | [nhmladakh.in](https://nhmladakh.in/HBP_2022.pdf) | 3,801 |
| `HBP_2.2_user_guidelines.pdf` | [hem.nha.gov.in](https://hem.nha.gov.in/HBP.pdf) | 64 |
| `CAG_PMJAY_Performance_Audit_Ch4.pdf` + `.txt` | [cag.gov.in](https://cag.gov.in) — Report No. 11 of 2023, Ch. 4 | 11 |
| `CAG_AUDIT_FINDINGS.md` | What the statutory auditor already found, and how to use it | — |
| `NHA_DeEmpanelled_Hospitals_2025-03.pdf` | NHA list, March 2025 — **Haryana only, 41 hospitals** | 2 |
| `PRIMARY_SOURCES.md` | Every quoted figure traced to a government source | — |
| `parliament/pib_*.html` | Raw PIB captures, so figures are verifiable without re-fetching | — |

**These are real, unstructured government prose** — the corpus the agents reason over. Even though
the claims are simulated, the agents' reference material is not.

Key locations:

| What | Where |
|---|---|
| Criteria relaxation clause | Empanelment Guidelines **§3.2.3** |
| Minimum criteria floor | Empanelment Guidelines **Annexure 1** |
| Aspirational districts list | Empanelment Guidelines **Annexure 4** |
| Disciplinary ladder | Empanelment Guidelines **§6.3** |
| Trigger-specific guidance | Anti-Fraud Guidebook **Annexure 2** |
| Letter templates | Anti-Fraud Guidebook **Annexure 4** |

---

## `scripts/`

| File | Purpose | Origin |
|---|---|---|
| `build_reference.py` | Regenerates the reference tables, with assertions | **This project** |
| `parse_hbp_master.py` | Parses the 3,801-page HBP master into `hbp_packages.csv` | **This project** |
| `build_lookups.py` | District points + trigger catalogue; guards the `NA` landmine | **This project** |
| `fetch_ogd.py` | Pulls the 21 official tables from data.gov.in; key redacted from the manifest | **This project** |
| `build_state_context.py` | Official tables → `state_context.csv`, `specialty_volume.csv`, `figures.json` "official"; reconciles the sources and asserts five figures | **This project** |
| `harmonise_pmjay.py` | How PM-JAY was placed on the district frame | Inherited |
| `build_crosswalk.py` | The reusable name-standardisation engine | Inherited |

The two inherited scripts are kept for **provenance and reproducibility** — they document how the
crosswalk was built. They do not need to be re-run.

---

## The simulated corpus — generated in memory, not stored

`generate/corpus.py` builds a year of **flagged** claims on demand (default 5,000, seeded, reproducible);
`python -m metrics.run` adjudicates them and writes `results/` and `docs/09-EVALUATION.md`. **Simulated**,
because claim-level PM-JAY data is not public. Nothing is written to `data/synthetic/`, so no generated
claim can be mistaken for data.

Where claims happen is **real** (state volumes, specialty mix, registry hospitals, package rates) and
the district allocation is **calibrated on real district admissions**. Conduct, documents and field
reports are **simulated**, with every assumption a named, swept parameter. Provider identity is
pseudonymous (`HOSP-nnnnn`), enforced by the `Claim` type. See
[04-DATA-DICTIONARY §8](../docs/04-DATA-DICTIONARY.md#8-flagged-claim-corpus--generatecorpuspy).

---

## What is not here, and why

| Not copied | Reason |
|---|---|
| OSM raw POI files (~100 MB) | Replacement-capacity idea dropped — OSM undercounts Indian hospitals badly (Bahraich: 87 PM-JAY vs 30 OSM). Finding C-04. Per-capita OSM columns survive inside `features_by_district.csv` |
| SECC, NFHS-5, HMIS raw tables | Already harmonised into `features_by_district.csv` |
| The MAI model outputs (`mai_scores.csv`) | Pharmaceutical market attractiveness — a different problem |
| Field Investigation & Medical Audit Manual | Referenced but not required by the build; linked in the README |
