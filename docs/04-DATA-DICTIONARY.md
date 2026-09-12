# 04 · Data Dictionary

**Audience:** Crew engineer, validation owner
**Rule:** every figure quoted in the report must be traceable to a row in this document.

---

## 1. Provenance summary

| Asset | Rows | Status |
|---|---|---|
| PM-JAY empanelled hospital registry | 35,286 | **Real** |
| District feature frame | 785 × 93 | **Real** |
| District geometry | 734 polygons | **Real** |
| NITI aspirational districts | 112 | **Real** |
| HBP 2022 package master | 3,801 pp | **Real** |
| NHA Anti-Fraud Guidebook | 164 pp | **Real** |
| NHA Empanelment Guidelines | 46 pp | **Real** |
| `district_adequacy.csv` | 11,528 cells | Real, derived |
| `access_distance.csv` | 2,185 cells | Real, derived |
| `hbp_packages.csv` | 3,058 packages | Real, derived |
| `hbp_package_rates.csv` | 1,602 packages | **Real** |
| `daycare_candidates.csv` | 95 packages | Derived, needs review |
| Official PM-JAY statistics (data.gov.in) | 21 tables | **Real** |
| `state_context.csv` | 36 states/UTs | Real, derived |
| `specialty_volume.csv` | 23 specialties | Real, derived |
| Flagged-claim corpus (`generate/corpus.py`) | 5,000 per run, in memory | **Simulated** on a real frame |
| Verification documents | per claim | **Simulated** |

---

## 2. Registry — `data/registry/pmjay_hospitals.parquet`

Source: hospitals.pmjay.gov.in, Registered Hospitals view, all-India export, July 2026.

| Field | Type | Notes |
|---|---|---|
| `hospital_id` | str | NHA identifier |
| `hospital_name` | str | **Real and identifiable — never used in scenario output (EC-1)** |
| `state` | str | Source vintage |
| `district` | str | Free-text, current vintage |
| `district_code` | int | LGD code via crosswalk; 99.3% matched |
| `hospital_type` | enum | Public · Private For Profit · Private Not For Profit · GOI |
| `empanelment_type` | enum | PMJAY · Only CGHS · State Specific · CAPF · combinations |
| `application_status` | enum | Approved for Empanelment · for Re-Empanelment |
| `specialities_selected` | list | Applied for |
| `current_specialities` | list | **Currently held — the field we use** |
| `hospital_contact` | str | 1,378 numbers are shared across ≥2 hospital ids |

### 2.1 Composition

| Hospital type | Count | Share |
|---|---|---|
| Public | 17,255 | 48.9% |
| Private (For Profit) | 15,193 | 43.1% |
| Private (Not For Profit) | 1,675 | 4.7% |
| GOI | 277 | 0.8% |

PMJAY-scope only: 31,198 rows. After excluding non-participating states: **30,858**.

### 2.2 Filters applied everywhere

```python
EMPANELMENT_SCOPE = "PMJAY"            # excludes Only CGHS, State Specific, CAPF
EXCLUDE_STATES = {"DELHI", "NCT OF DELHI", "ODISHA", "WEST BENGAL"}
```

**Both filters must be applied identically in every analysis.** Quoting an unfiltered figure
alongside a filtered one is the easiest way to get caught with inconsistent numbers.

---

## 3. Specialty codes

The registry uses HBP 1.0 codes. Mapping extracted from the HBP 2022 package master.

**Authoritative source:** the `searchSpeciality` dropdown on `hospitals.pmjay.gov.in/Search/` — the
same system the registry export comes from. **89 codes**, captured in
`data/reference/specialty_legend.json`. This supersedes the earlier PDF-parsed legend, which had
five wrong.

| Code | Specialty | Code | Specialty |
|---|---|---|---|
| M1 · MG | General Medicine | S5 · SB | Orthopaedics |
| M2 · MP | Paediatric Medical Management | **S6 · ST** | **Polytrauma** |
| M3 · MN | Neo-natal Care | S7 · SU | Urology |
| M4 · NA | Paediatric Cancer | S8 · SN | Neurosurgery |
| M5 · MO | Medical Oncology | S9 · IN | Interventional Neuroradiology |
| M6 · MR | Radiation Oncology | S10 · SP | Plastic & Reconstructive Surgery |
| M7 · ER | Emergency Room Packages | S11 · BM | Burns Management |
| M8 · MM | Mental Disorders | S12 · MC | Cardiology |
| S1 · SG | General Surgery | S13 · SV | Cardio-thoracic & Vascular Surgery |
| S2 · SL | Otorhinolaryngology (ENT) | S14 · SS | Paediatric Surgery |
| S3 · SE | Ophthalmology | **S15 · SC** | **Surgical Oncology** |
| S4 · SO | Obstetrics & Gynaecology | S16 · SM | Oral & Maxillofacial Surgery |

**Corrections the authoritative source forced** — the PDF parse had conflated names across wrapped
table rows:

| Code | PDF parse said | Actually |
|---|---|---|
| **S6** | Orthopedics, secondary band | **Polytrauma** |
| **S15** | General Surgery | **Surgical Oncology** |
| S2 | ENT, Surgical Oncology | Otorhinolaryngology |
| S7 | Urology, Paediatric Surgery | Urology |
| S9 | Interventional Radiology | Interventional Neuroradiology |

S12 (Cardiology), S8 (Neurosurgery) and S13 (CTVS) are confirmed unchanged — **the Bahraich cardiology
case stands.**

**`data/reference/hbp_packages.csv`** — 3,058 packages across 38 HBP 2022 specialty codes, parsed
from the package master by `scripts/parse_hbp_master.py`. Columns: `package_code`, `specialty_code`,
`specialty_name`, `hbp1_code`, `registry_code`. 870 packages carry the legacy HBP 1.0 code that maps
back to the registry's specialty field. **This is what `hbp_lookup` reads.**

*Known imperfection:* a handful of specialty names in the parsed table have procedure text bleeding
into them, an artefact of the PDF's line wrapping. The registry-code mappings — the part the
pipeline depends on — are correct and were cross-checked against the independent extraction.

**Tertiary set** used by `capability_flag`: `{S12, S9, S8, M6, S13, OT}`

**Excluded from adequacy scoring.** Two grounds, and the legend now lets us separate them:

| Code | Name | Districts | Why excluded |
|---|---|---|---|
| `CP` | Consultations & Procedures | 1 | **Not an inpatient specialty** — OPD category |
| `OC` | OPD Consultations, Procedures & Investigations | 1 | **Not an inpatient specialty** |
| `M10` | OPD Diagnostic | 11 | **Not an inpatient specialty** |
| `OT` | Organ & Tissue Transplant | 23 | Genuinely rare; no district can be expected to hold it |
| `TG` | Gender Affirming Medical and Surgical Treatments | 24 | Genuinely rare |
| `JR` | Joint Replacement | 29 | Genuinely rare |

The first three are **OPD and diagnostic categories, not inpatient specialties** — a stronger reason
to exclude them than rarity alone. Of the 89 codes, **25 are non-inpatient**; the legend flags them
so adequacy scoring can exclude the category rather than chase a district count.

---

## 4. `district_adequacy.csv`

Precomputed. One row per occupied district × specialty cell.

| Field | Type | Description |
|---|---|---|
| `district_code` | int | LGD code |
| `spec` | str | Registry specialty code |
| `n_providers` | int | Distinct hospitals offering it in the district |
| `n_private` | int | Of which Private For Profit |

**11,528 cells** after filters.

| Band | Cells | Share |
|---|---|---|
| 1 provider | 2,202 | 19.1% |
| ≤2 providers | 3,541 | 30.7% |
| ≤3 | 4,564 | 39.6% |
| ≤5 | 5,873 | 50.9% |
| ≥10 | 3,835 | 33.3% |
| ≥20 | 1,931 | 16.8% |

### 4.1 Concentration by specialty

| Specialty | Districts served | Sole-provider | Share |
|---|---|---|---|
| General Medicine | 663 | 32 | 4.8% |
| Obstetrics & Gynaecology | 648 | 34 | 5.2% |
| Emergency Room Packages | 657 | 40 | 6.1% |
| General Surgery | 635 | 42 | 6.6% |
| Orthopaedics | 618 | 58 | 9.4% |
| Paediatric Medical Management | 619 | 64 | 10.3% |
| Urology | 499 | 77 | 15.4% |
| Otorhinolaryngology (ENT) | 574 | 94 | 16.4% |
| Ophthalmology | 607 | 100 | 16.5% |
| Neo-natal Care | 473 | 110 | 23.3% |
| Neurosurgery | 428 | 100 | 23.4% |
| Oral & Maxillofacial Surgery | 532 | 130 | 24.4% |
| Polytrauma | 431 | 105 | 24.4% |
| Interventional Neuroradiology | 376 | 92 | 24.5% |
| Burns Management | 451 | 111 | 24.6% |
| Cardiology | 432 | 107 | 24.8% |
| Plastic & Reconstructive Surgery | 442 | 114 | 25.8% |
| Paediatric Surgery | 407 | 108 | 26.5% |
| **Cardio-thoracic & Vascular Surgery** | 306 | 83 | **27.1%** |
| **Medical Oncology** | 380 | 107 | **28.2%** |
| **Surgical Oncology** | 377 | 114 | **30.2%** |
| **Paediatric Cancer** | 292 | 100 | **34.2%** |
| **Radiation Oncology** | 308 | 108 | **35.1%** |
| **Mental Disorders** | 373 | 172 | **46.1%** |

Cardiology (S12): **107 of 432 districts** have exactly one provider; 67 have ≥15.

---

## 5. `access_distance.csv`

For every sole-provider cell, the distance to the nearest **other** district offering that specialty.

| Field | Type | Description |
|---|---|---|
| `district_code` | int | LGD code |
| `spec` | str | Specialty |
| `km_to_alternative` | float | Haversine between district representative points |
| `district`, `state` | str | Labels |
| `pop_now` | float | Current population estimate |

**2,185 rows.**

| Percentile | km |
|---|---|
| p25 | 36.8 |
| **p50** | **50.7** |
| p75 | 66.2 |
| p90 | 86.4 |
| p95 | 104.0 |

| Threshold | Cells | Share | Districts | Population |
|---|---|---|---|---|
| >50 km | 1,108 | 50.7% | 310 | 584 M |
| >100 km | 130 | 5.9% | 52 | 79 M |
| >150 km | 54 | 2.5% | 12 | 12 M |

**Method note for the report:** straight-line between district representative points. Real road
travel is roughly 1.3–1.5× further, so every figure is conservative.

---

## 6. District features — `district_features.parquet`

785 districts × 93 indicators, harmonised to the LGD frame from NFHS-5, HMIS, NCRP, SECC, GHS-POP,
RBI, NMC and OSM sources.

Fields the pipeline uses:

| Field | Use |
|---|---|
| `district_code`, `district_name_english`, `state_name_english` | Keys and labels |
| `log_pop_now` | Population = `10 ** log_pop_now` |
| `pmjay_hospitals_per100k` | Context |
| `health_insurance_pct` | Context |
| `oop_delivery_rs` | **Out-of-pocket handle for the cost model** |
| `diabetes_prev_pct`, `hypertension_prev_pct`, `cancer_incidence_per100k` | Incidence for the cost model |
| `is_post2011_district` | **Flags the new-district artefact** |
| `nfhs5_coverage`, `hmis_coverage` | Source coverage, for caveats |

---

## 7. Aspirational districts

NITI Aayog list of 112. File is comment-prefixed — read with `comment="#"`.

| | |
|---|---|
| Present in the network | 100 of 112 |
| **Sole-provider rate** | **27.0%** |
| Rate elsewhere | 24.5% |

---

## 8. Flagged-claim corpus — `generate/corpus.py`

> **As built (16 September 2026).** The corpus is generated in memory by `generate/corpus.py` and measured by
> `python -m metrics.run`; results in [09-EVALUATION](09-EVALUATION.md). It simulates **flagged** claims only —
> the pipeline's input — at the share the base rate implies: 0.18% fraud, 1% flag FPR, 90% recall → **14.0% of
> flags are fraud**. Each case fires exactly its intended trigger (asserted in `tests/test_corpus.py`). The
> sections below record the original plan; §8.3 is what was built.

**Simulated.** Generated against the real hospital network with pseudonymous provider identity.

Schema: see [02-LLD §2](02-LLD.md#2-contracts-crewschemaspy).

### 8.1 Planted patterns — publish this table in the report

| Pattern | Construction | Trigger | Severity |
|---|---|---|---|
| Zero-LOS surgical | `los_days = 0`, major surgical package | 2 | 2 |
| Zero-LOS medical | `los_days = 0`, specialty ∈ {M1, M2} | 3 | 2 |
| Extended stay | `los_days > 10`, `icu_flag = false` | 4 | 1 |
| Impossible surgeon | same `surgeon_reg_no`, same date, districts >200 km apart | 5 | 3 |
| Document reuse | identical hash, ≥2 claims, different beneficiaries | 6 | 3 |
| Repeat acute episodes | ≥3 acute claims, one beneficiary, 30 days | 7 | 2 |
| Post-death service | `admission_ts` after recorded death | 10 | 3 |
| **Specialty not empanelled** | package specialty ∉ **real** registry specialties | R1 | 3 |
| **Clean decoys** | genuine day-care packages with short stays | — | — |

The last two rows carry disproportionate weight. **R1 is detectable entirely from real published
data.** The decoys exist so the false-positive rate is *measured* rather than assumed — which is the
difference between "we found our own traps" and a validation.

### 8.2 Composition target (original plan — superseded by §8.3)

| Class | Count | Purpose |
|---|---|---|
| Clean, unremarkable | ~1,400 | Base rate |
| Clean decoys | ~200 | False-positive control |
| Planted fraud | ~350 | Trigger coverage, ≥30 per pattern |
| Malformed | ~50 | Error path |

---

### 8.3 As built — what each trigger's cases look like

| Trigger | Fraud mechanism | Innocent mechanism | What decides it |
|---|---|---|---|
| T2 / T3 | phantom procedure or admission, no explanation on file | same-day discharge; LAMA form on file 60% of the time | desk if explained; otherwise two / one field channels |
| T4 | stay padding, no clinical justification | complication documented 60% of the time | desk |
| T5 | ghost surgeon | registration number keyed wrongly | two field channels |
| T6 | discharge summary reused across beneficiaries | *none assumed* (limitation 1 in 09-EVALUATION) | desk |
| T7 | phantom repeat admissions | genuine chronic illness | one field channel |
| T10 | billing after death; certificate on file 80% of the time | death-register error, no certificate | desk with certificate; otherwise two field channels |
| R1 | billing a specialty not empanelled for | capable but never applied (CAG §4.5) — still a breach | registry |
| R2 | upcoding, no invoice | implant cost; invoice on file 60% of the time | desk |
| R3 | reserved package misused, no referral | government referral on file 60% of the time | desk |

Every claim carries its package's mandatory documents (pre-procedure evidence; an operative or procedure note where the package requires one), as the claim system requires at submission.

Placement is real: state ∝ admissions 2024-25; specialty ∝ official specialty volumes; district ∝
√(providers) × √(population), fitted to UP and Gujarat district admissions; hospital uniform among the
district's providers; package from the published master. Field reports point the right way 85% of the
time with confidence uniform 0.60–0.95. Every assumption is a `CorpusConfig` field, swept in
09-EVALUATION §7.

---

## 9. Decision log — `out/decision_log.csv`

One row per case.

The columns, in order, are `crew/actions.py` `LOG_FIELDS`. Lists are `|`-separated.

| Field | Type | Notes |
|---|---|---|
| `case_id`, `claim_id`, `hospital_ref` | str | Keys; the hospital by pseudonym only (EC-1) |
| `trigger_id`, `triggers_fired`, `severity` | str, list, int | The governing trigger (`T10`, `R1`, …), every trigger that fired, its severity band |
| `action`, `claim_withheld`, `human_required` | enum, bool, bool | Terminal action; a human is required for escalation, de-listing referral and review |
| `gate`, `confidence`, `degraded` | str, float, bool | `clear` / `protect` / `phantom` / `unknown` for egregious findings; aggregated confidence; rules decided (no model, or the model failed) |
| `channels_used`, `field_channels_ordered` | list, list | **Proves the branching — differs across cases** |
| `district`, `specialty`, `n_providers`, `km_to_alternative`, `population`, `aspirational`, `capability_ok` | — | Adequacy of the **billed** specialty |
| `specialties_at_stake` | list | **Every specialty a suspension would strip of its only real provider** — suspension removes a hospital from all of them, so the gate checks all (F-22) |
| `reason_codes`, `conflicts` | list, str | Machine-readable justification; where findings disagreed |
| `artefact_path`, `decided_ts`, `model` | str, datetime, str | The notice or brief; the 24-hour trigger SLA after submission (reproducible); the model the crew ran on |
| `acted_by` | str | `enforcement_officer` when the officer executed the decision through its action tool; `orchestrator` when the decision was executed with the template explanation (a rules run, an outage, or an officer that did not act) |
| `tools_called` | list | The investigation trail in brief: `agent:tool` for every tool call, `:refused` appended when the tool declined (a wrong action, a rejected draft or brief, a claim outside the case) |
| `agents` | list | The agents whose tasks ran to completion, in order: `case_router`, `desk_investigator`, `medical_auditor` (T2, T3, T4, T7, or the router), `field_evidence_analyst` (field reports on file), `billing_analyst` (R2, above-rate claims, or the router), `provider_advocate` (when a reading supports fraud, or the router), `audit_reviewer`, `committee_liaison` (escalations and de-listing referrals), `enforcement_officer`. Empty on a degraded decision: the rules decided it |
| `disputed` | list | The findings the Audit Reviewer disputed (`desk_audit`, `medical_audit`, `billing_audit`, `advocacy`). A disputed finding weighs nothing in the decision |

### 9a · On the Decision and in the artefact, not in the log

The nine-agent crew records more than the log's 31 columns carry. These are fields of `Decision`
(`crew/schemas.py`), rendered into each case's artefact by `crew/actions.py`:

| Field | Type | Notes |
|---|---|---|
| `opened_by_router` | list | The optional tracks the Case Router opened that no rule made mandatory (`medical`, `field`, `billing`, `advocate`). Empty on a rules-only or degraded decision (B-48) |
| `router_reasons` | list | Why it opened each, as `track: reason`. The artefact prints these under "Opened by the Case Router" |
| `defence` | string or null | The Provider Advocate's innocent explanation, when an advocate worked the case. Null when none did |
| `defence_excluded` | bool or null | Whether the evidence on file excluded that explanation. `false` means it stood, and no action was taken that no human has reviewed (B-49). Null when no advocate ran |
| `disputes_refused` | list | The findings the Audit Reviewer tried to dispute where the dispute was refused, because the reading repeats a comparison the claim store makes itself (B-44a, F-61). Such a reading keeps its stance and confidence |
| `review` | string or null | The Audit Reviewer's summary of its review |
| `committee_brief` | object or null | The Committee Liaison's filed brief: summary, options with consequences, recommendation, question |
| `trail` | list | Every tool call in full — agent, tool, arguments, result size, refusals — of which the log keeps only the `agent:tool` summary |

Per-agent findings with their citations, and the full investigation trail, are in each artefact rather than the log.
A rejected draft or brief appears in the trail as its length and the problems found, never its words. The 40-case
agent benchmark records the crew-specific fields per case in `results/agent_benchmark_<provider>.csv`
([10-AGENT-EVALUATION](10-AGENT-EVALUATION.md)).

---

## 10. Known data defects

**All three must be handled in code and declared in the report.**

### D-1 · Non-participating states

| State | PMJAY hospitals | Population (M) | Per million |
|---|---|---|---|
| Delhi | 73 | 22.2 | 3.29 |
| Odisha | 12 | 47.7 | 0.25 |
| West Bengal | 43 | 103.2 | 0.42 |
| *Bihar, next lowest* | *1,115* | *133.1* | *8.38* |

No official admission table through 2024-25 lists any of the three (the Rajya Sabha answers cover 33
states/UTs), so they were not operating the scheme in the period our volumes cover. Their 2025 networks
match the Ministry's count exactly (73, 12, 43 as on 1 March 2025). **Excluded everywhere via
`EXCLUDE_STATES`.** *Corrected 16 September 2026: Delhi was previously shown with 0 hospitals because the
registry spells it `NCT OF Delhi`.*

### D-2 · District vintage

Tenkasi, Ranipet, Mayiladuthurai, Kallakurichi (2019–20), Konaseema, Bapatla (2022) all appear at
the bottom of per-capita tables. The registry's district assignment lags reorganisation. Use
`is_post2011_district` to flag, and verify before quoting any individual district on a slide.

### D-3 · Entitlement, not capability

12.8% of CHC/PHC-named facilities list a tertiary specialty. Concentration:

| State | CHC/PHC entries | Listing tertiary |
|---|---|---|
| Punjab | 136 | **94.9%** |
| Gujarat | 1,531 | **82.7%** |
| Telangana | 735 | 12.5% |
| Himachal Pradesh | 49 | 4.1% |
| Haryana | 396 | 2.0% |
| Rajasthan · Kerala · Jharkhand · J&K | 1,026 | **0.0%** |

**Those three states account for 95% of all such listings.** This is a state-level bulk-empanelment
convention, not facility-level misrepresentation.

Two consequences:

1. It **cannot** be used as a fraud trigger — `capability_flag` must return `state_convention=True`
   and `plausible=True` for these states, or the system becomes a false-positive generator.
2. Our access figures are **optimistic**. Real coverage is thinner than 19.1% suggests, so the
   argument is conservative in the right direction.

Forty district-specialty cells have a CHC or PHC as sole listed provider of a tertiary specialty —
including a PHC listed as its district's only radiation oncology provider.

### D-4 · Thirty percent of hospitals list no specialty at all

**9,265 of 30,858 PMJAY-scope hospitals (30.0%) have `Current Specialities = -NA-`**, spanning 285
districts. Mostly small public facilities — district Ayurvedic hospitals, CHCs, PHCs.

They contribute nothing to any adequacy count. Combined with D-3, the registry's specialty field is
unreliable **in both directions**: overstated in Punjab and Gujarat, entirely absent for a third of
the network nationally.

This reinforces the design posture rather than undermining it. It is precisely why the system
**flags for verification** rather than concluding, and why capability is a flag and not a finding
(EC-3). Whether it makes our access figures conservative or optimistic depends on whether those
hospitals actually deliver anything under PM-JAY — which cannot be established from the registry, and
should be stated as an open question rather than assumed either way.

### D-5 · `NA` is a specialty code, and pandas eats it

**`NA` = Paediatric Cancer.** `pd.read_csv` converts the string `"NA"` to `NaN` by default, silently
deleting **50 legitimate cells** from `district_adequacy.csv`.

```python
pd.read_csv("data/reference/district_adequacy.csv", keep_default_na=False)   # correct
pd.read_csv("data/reference/district_adequacy.csv")                          # loses 50 cells
```

We hit this: a "hygiene fix" that dropped null specialties destroyed those rows before anyone
noticed. **Every consumer of the specialty tables must pass `keep_default_na=False`.**
`build_reference.py` now asserts `"NA" in cell.spec`, and `build_lookups.py` reports how many cells
a careless read would lose. It is a landmine precisely because it fails silently and the count still
looks plausible.

---

## 9a. Package rates and documentary requirements

**`data/reference/hbp_package_rates.csv`** — 1,602 packages across 23 specialties, from Punjab SHA's
public package master. Every row carries a rate **and the documents that must exist**.

| Field | Example |
|---|---|
| `package_code` | `MC017A` |
| `package_name` | Peripheral Angioplasty |
| `package_amount_rs` | 34,500 |
| `pre_investigations` | ECHO, Doppler, Angio stills showing blocks & Reports |
| `post_investigations` | Post-op Angiogram report / stills showing stent; Implant / barcode of stent used |
| `govt_reserved` | No |

| | |
|---|---|
| Rate range | ₹0 – ₹208,600, median ₹15,100 |
| Reserved for government hospitals | 182 |
| **With pre- and post-investigation requirements** | **1,602 — all of them** |

Median rate by specialty runs from CTVS at ₹119,000 and Interventional Neuroradiology at ₹70,000
down to General Surgery at ₹16,000.

**Mandatory document categories** (`crew/investigate.py` `required_documents()`, F-56). A desk, the rules or the crew, may clear a claim only when every category the package's entry names is on file, under a document type of that kind:

| Category | When required | Satisfied by a document type containing | Packages |
|---|---|---|---|
| Discharge summary | always | `discharge_summary` | 1,602 |
| Pre-procedure evidence | a pre-investigation requirement is stated | clinical notes, investigation, lab, radiology, endoscopy or histopathology reports | 1,588 |
| Operative or procedure note | the post requirement names operative, operation or procedure notes | `operative_note`, `procedure_note` | 1,006 |
| Histopathology | histopath, HPE | histopath, hpe, biopsy | 355 |
| Clinical photograph | photo, still image, stills | photo, still, image | 788 |
| Post-procedure imaging | X-ray, CT, MRI, USG, imaging, angiogram, Doppler, echo | x-ray, radiology, imaging, CT, MRI, USG, echo, angiogram, Doppler, scan | 248 |
| Investigation reports | investigation, reports of the tests, lab tests | investigation, lab, pathology, culture, radiology, imaging | 455 |
| Indoor case papers | ICPs, case papers, progress notes | progress, case papers, case sheet, ICP, indoor, treatment chart, nursing | 216 |

The check reads categories, not dates: a pre-operative X-ray report satisfies post-procedure imaging. Implant barcodes are not checked here (the master asks for them "if used"); an implant invoice is R2's evidence. The corpus files every category a claim's package requires, so the stricter check changed no evaluation figure.

### Why this changes the build

1. **The desk-audit agent gets a real checklist.** `pre_investigations` and `post_investigations`
   are, per package, exactly what NHA's trigger guidance means by *"verify mandatory documents for
   blocked procedure."* The agent no longer checks documents against a generic rule — it checks them
   against the named requirement for that procedure.
2. **A new detection becomes possible on real data:** claimed amount versus the published package
   rate. Upcoding and amount inflation are now checkable, not just describable.
3. **Trigger 2 becomes well-defined.** *Zero LOS on a **major surgical** package* needs a definition
   of "major". **694 packages at ≥ ₹20,000 and not day-care** is that definition.

**Caveat to state:** these are Punjab's HBP 2.0 rates under AB PM-JAY MMSBY. Other states vary, and
HBP 2022 introduced tiered pricing by city tier. Quote them as *"Punjab SHA published rates"*, not as
national rates.

### `daycare_candidates.csv` — 95 packages, and an honest limitation

**Rebuild:** `python scripts/build_packages.py` (which also rebuilds `hbp_package_rates.csv`, byte for byte).

Same-day discharge is legitimate for some packages, so the zero-LOS triggers cannot fire on all of
them. Candidates are identified three ways, in this order: the **6 ER packages** (NHA defines them as
*"care requiring less than 12 hrs stay"*); **30 whose own name says same-day care** — follow-up, dialysis,
transfusion, injections, dressing sessions, "not requiring admission" — read as whole words in the package's name
before any `:` or `;`, where inclusions and eligibility conditions begin; and **59 priced above ₹0 and at most
₹3,000**.

**Packages sit under several specialties (D-12).** 473 of the 1,602 packages are listed under two to four
specialties (a paediatric package under both General Medicine and Paediatric Medical Management, say), and 52 are
government-reserved under one listing and open under another. `hbp_package_listings.csv` keeps every listing
(2,196 rows: `package_code`, `specialty_code`, `govt_reserved`, `referral_basis`); a package's own specialty is
the one its code names. Rates and required documents never differ between listings, and the build asserts it.
`referral_basis` is published only for reserved listings: **9 may be billed by a private hospital on a government
referral** (caesarean and high-risk deliveries, two eye repairs); **180 may not**, whatever referral is on file (F-51).

**The first version of the name rule misfired (D-11).** It matched substrings anywhere in the text, so "follow-up
dressings" *included* in a skin-grafting package, "opd" inside COPD and "the following conditions" of a neonatal
intensive-care package all read as same-day care. Twenty-four inpatient packages were on the list — 17 burns
surgeries of ₹30,000–80,000, acute exacerbation of COPD, respiratory failure — so a zero-length stay on them could
never fire trigger 2 or 3. They are off the list now, and `build_packages.py` asserts they stay off.

**It is still a candidate list for clinical review, not a definitive one** — and say so in the report. A student
team is not qualified to rule on which procedures are day-care, and pretending otherwise is exactly the kind of
unearned confidence the argument warns against elsewhere.

---

## 9b. De-empanelled hospitals — partial ground truth

**`data/reference/deempanelled_2025_03.csv`** — 41 hospitals, parsed from NHA's *List of
De-Empanelled Hospitals Under AB-PMJAY*, March 2025.

**It is Haryana only.** Against 2,359 national de-empanelments it is roughly 1.7% of the total.
Treat it as a case sample, not a census.

### What it does support

| Hospital type | Share of network | Share of de-empanelments | Ratio |
|---|---|---|---|
| **Private (For Profit)** | 40.7% | **82.9%** | **2.0×** |
| Private (Not For Profit) | 4.1% | 7.3% | 1.8× |
| **Public** | 54.6% | **9.8%** | **0.2×** |
| GOI | 0.6% | 0.0% | — |

**Enforcement falls twice as heavily on private for-profit hospitals as their share of the network,
and public hospitals are de-empanelled at a fifth of their share.** This needs no name matching —
it uses all 41 rows — and it belongs in the disparate-impact discussion. Whether it reflects
differential misconduct or differential scrutiny is not something this data can settle, and the
report should not pretend otherwise.

### What it does NOT support — and why we are not reporting it

We attempted the obvious retrospective test: *were de-empanelled hospitals disproportionately sole
providers?* The raw answer came back 0 of 14 cells against a 19.1% national rate. **We are not
reporting that number**, for three reasons:

1. **Only 10 of 41 hospitals matched the registry** — the IDs use a different scheme
   (`HOSP6P66487` vs `02001018`), so matching is by name and district. The 31 that did not match may
   well be unmatched *because they were removed from the registry on de-empanelment*, which makes
   the matched 10 a biased subsample.
2. **The adequacy table is the July 2026 network; the de-empanelments are March 2025.** Asking
   whether a removed hospital is currently a sole provider is close to circular.
3. **Fourteen cells from one relatively well-served state** cannot distinguish a real effect from
   noise.

The test is worth re-running if a national list with matching IDs becomes available. Reporting a 0%
from this sample would be exactly the unearned confidence the argument criticises elsewhere.

### D-6 · One specialty, two code vintages — the error that broke the demo

The registry records the same specialty under **HBP 1.0 and HBP 2022 codes** — `S12` and `MC` both
mean Cardiology — and hospitals list **one or the other, almost never both**. For cardiology, 3,457
hospitals list only `S12`, 465 list only `MC`, and none list both.

Version 1 of `build_reference.py` counted each code as a separate specialty, so a district with one
`S12` hospital and two `MC` hospitals showed a "sole cardiology provider" when it has three. **Every
adequacy figure was overstated**, and the original demo district — Gaya — turned out to have three
cardiology providers, not one.

| Measure | v1 (raw codes) | **v2 (canonical)** |
|---|---|---|
| Occupied cells | 15,462 | **11,528** |
| Sole-provider share | 25.6% | **19.1%** |
| Median km to alternative | 52.2 | **50.7** |
| Aspirational vs elsewhere | 32.6% vs 24.5% | **27.0% vs 17.9%** |
| Demo case | Gaya — "1" provider (actually 3) | **Bahraich — 1 provider, verified** |

**Fix:** 48 registry codes map to 24 canonical specialties in `specialty_canonical.csv`, and the demo
case is now **asserted** in the build — if Bahraich ever stops being a sole cardiology provider, the
build fails. Caught before any code depended on it.

**What survived:** nearly one in five cells still has a single provider, and the aspirational gap
*widened* in relative terms — from 1.33× to 1.51×.

---

### D-7 · Two official admission series do not reconcile

The cumulative *authorised admissions 2019-20 to 2024-25* answer sums to **78.3%** of the
year-wise *hospital admissions* answer over the same years. The year-wise series agrees exactly with a
third answer (2021-22 to 2023-24), so **we use the year-wise series**; the cumulative one is kept in
`data/external/ogd/` but not used. Quote admissions with their table.

### D-8 · The registry export is undated

Every `Submitted Date` and `Status Updated Date` in the export is `-NA-`. The export was pulled on 16 July
2026, but its PMJAY-scope count matches the Ministry's 1 March 2025 figure within +0.8% (21 of
36 states exact). **Describe the network as of roughly early 2025**, not as of July 2026.

### D-9 · Two amount tables omit their unit

`admissions_amount_2018_2025` and `admissions_count_amount_2021_2024` publish amounts without a unit. We
read them as **Rs crore**: Rs lakh would imply an average claim of Rs 157, and Rs crore gives
Rs 15,735, consistent with the specialty answer's independently stated Rs crore totals.

### D-10 · "Non-admissible" is not a fraud rate

`claims_nonadmissible_abuse_private` is **value, not count**, covers **private hospitals only**, mixes abuse,
misuse **and incorrect entries**, and lists 23 states (Karnataka, Tamil Nadu, Andhra Pradesh and
Rajasthan are absent — not zero). As a share of each state's claim value it ranges from 0.0%
(Maharashtra) to 1.584% (Uttar Pradesh). Use it to show that **enforcement intensity varies by
orders of magnitude across states**, never as a prevalence estimate.

### D-11 · A day-care list built on substrings hid inpatient packages from two triggers

See §9a. "follow" matched "follow-up dressings" inside ₹80,000 burns surgeries and "the following conditions" of
neonatal intensive care; "opd" matched COPD. 24 inpatient packages were marked day-care, so triggers 2 and 3 could
never fire on them. Rebuilt by script with a word-bounded rule on the package's own name; nothing else changed.

### D-12 · One package, several specialties

See §9a. Reading only the first-sorted listing made R1 accuse 15,419 hospitals of billing packages they are
empanelled for, and misread the reserved flag of 52 packages. Every listing is now kept and used (F-35).

---

## 10a. Base rate

> "Around **0.18%** of the total authorized hospital admissions under the scheme are confirmed as
> fraud since its inception." — PIB PRID 1847423, 2 August 2022

**Calibrate the synthetic corpus against this.** A generator that plants fraud in 20% of claims
produces a world that does not exist, and any precision figure measured in it is meaningless.

It also carries the argument: at a 0.18% prior, a detector at a 1% false-positive rate yields flags
that are ~86% innocent. See
[PRIMARY_SOURCES §1](../rulebooks/PRIMARY_SOURCES.md#1-the-base-rate--and-why-it-decides-the-design).

---

## 11. Reference case — Bahraich, Uttar Pradesh

The demo anchor. Every figure verified.

| | |
|---|---|
| Population | 4.16 M |
| NITI aspirational | Yes |
| Empanelled hospitals | 87 — 74 Public, 12 Private For Profit, 1 Not For Profit |
| Specialties available | 17 of 24 canonical |
| **Specialties with one provider** | **3** — Cardiology, Plastic & Reconstructive Surgery, Surgical Oncology |
| Authorised admissions, 2021-22 | **8,990** — UP district median 4,741; Gonda, the nearest alternative, 8,085 |

*Corrected 16 September 2026 to the canonical-specialty registry (D-6); every figure here is asserted by
`build_reference.py` or `build_state_context.py`.*

| Specialty | Providers |
|---|---|
| General Medicine | 80 |
| Emergency Room Packages | 35 |
| Obstetrics & Gynaecology | 17 |
| Paediatric Medical Management | 13 |
| General Surgery | 13 |
| Orthopaedics | 11 |
| Neo-natal Care | 8 |
| Urology | 7 |
| Otorhinolaryngology (ENT) | 5 |
| Paediatric Surgery | 4 |
| Ophthalmology | 4 |
| Oral & Maxillofacial Surgery | 4 |
| Neurosurgery | 3 |
| Polytrauma | 2 |
| **Cardiology** | **1** |
| **Plastic & Reconstructive Surgery** | **1** |
| **Surgical Oncology** | **1** |

Nearest district with another cardiology provider: **83 km**.

**Contrast case — Ahmedabad, Gujarat:** 288 empanelled hospitals, **101** offering cardiology.

---

## 12. Official statistics — `state_context.csv`, `specialty_volume.csv`

Built by `scripts/build_state_context.py` from `data/external/ogd/`. **Real, derived.**

### `state_context.csv` — one row per state/UT (36)

| Column | Meaning | Source table |
|---|---|---|
| `state`, `state_name` | Canonical key (upper case; Dadra & Nagar Haveli and Daman & Diu merged) and display name | — |
| `admissions_2024_25`, `amount_cr_2024_25`, `avg_claim_rs_2024_25` | Hospital admissions, amount (Rs crore, D-9), average claim | `admissions_2018_2025`, `admissions_amount_2018_2025` |
| `admissions_2018_25`, `amount_cr_2018_25` | Seven-year totals | same |
| `expected_confirmed_fraud_2024_25` | admissions × 0.18% | PIB PRID 1847423 |
| `expected_innocent_flags_2024_25` | admissions × 99.82% × 1% FPR | arithmetic |
| `nonadmissible_private_cr`, `nonadmissible_share_of_amount_pct` | Non-admissible claim value, private hospitals, to 14-01-2025; share of 2018-25 amount (D-10) | `claims_nonadmissible_abuse_private` |
| `deempanelled_public_2018_25`, `deempanelled_private_2018_25` | De-empanelments by ownership | `hospitals_deempanelled_by_ownership` |
| `empanelled_public_2018_25`, `empanelled_private_2018_25` | Empanelments by ownership (flows, not stock) | `hospitals_empanelled_by_ownership` |
| `opted_out_2019_25` | Voluntary exits | `hospitals_opted_out` |
| `single_mobile_beneficiaries_2018_21` | Beneficiaries sharing one mobile number | `beneficiaries_single_mobile` |
| `mental_health_admissions_2022` | Mental-health admissions (answer of 15-03-2022) | `admissions_mental_health` |
| `official_hospitals_2025_03` | Empanelled hospitals as on 01-03-2025 | `hospitals_empanelled_2025_03` |
| `registry_hospitals_raw`, `registry_hospitals_in_scope`, `registry_public_pct` | Our export: PMJAY scope; after placement and state exclusion; public + GOI share | registry |
| `registry_minus_official` | Validation gap (D-8) | derived |

Empty cells mean the table does not list the state — **not zero**.

### `specialty_volume.csv` — one row per canonical specialty (23)

| Column | Meaning |
|---|---|
| `specialty`, `official_label` | Canonical specialty and the label the answer used |
| `admissions`, `amount_cr` | Authorised admissions and amount (cumulative to 30-06-2024) |
| `basis` | `2024-06-30 answer`, or `2021-02-02 answer x 5.17` for Burns, Interventional Neuroradiology, Mental Disorders and Oral & Maxillofacial Surgery, which the 2024 answer omits; the scale is the ratio of the two answers over the 15 specialties both report |
| `share_pct`, `avg_claim_rs` | Share of admissions; average claim |

Paediatric Cancer has no row: both answers fold it into oncology.

