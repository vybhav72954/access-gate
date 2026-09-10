# Forward Crosswalk — Our Three Datasets → LGD Current-District Frame

Everything in this note is produced by **`scripts/build_crosswalk.py`** (re-runnable; reads
`data/raw/` + the LGD × SHRUG backbone views in `data/processed/district_frame/`, and writes all
its outputs to **this folder, `data/processed/crosswalk/`**). It maps our three assigned procurement
rows onto the **current 785-district LGD frame** so they line up with Pranav's LGD ↔ SHRUG backbone
(`district_frame/view1`–`view4`, `district_frame/district_parent_map.csv`).

**File-naming standard (this folder).** All files are lower-case `snake_case`, `<subject>_<detail>.csv`,
with no `our_`/`xwalk_` prefixes (the folder name already says *crosswalk*): the consolidated headline
table is `consolidated_by_district.csv`; per-source tables are `<source>_<detail>` (`rhs_*`, `nmc_*`,
`rbi_*`, `slbc_mp`, `nabard_wb`); crosswalk machinery is `name_map`, `unmatched_report`,
`nmc_manual_placements`, `nmc_pg_manual_placements`.

**Teammate pulls are harmonised here too.** Besides our three rows, the already-pulled *teammate*
district datasets in `data/raw/` (HMIS facility counts, Jan Aushadhi kendras, and the 7 SECC-2011
tables) are put on the same 785 frame by a sibling script, **`scripts/harmonise_team_sources.py`**,
which just calls the reusable `crosswalk_file()` engine. Their outputs keep the same naming standard
with a source prefix (`hmis_*`, `janaushadhi_*`, `secc_*`) and are listed in §5b. State-level pulls
(PM-JAY, public-health-infrastructure) are *not* here — they can't be placed on a district frame.

Dependencies: `pandas`, `numpy`, `rapidfuzz`, `openpyxl`.

---

## 1 · Why a name-crosswalk (not a code-join) was needed

Pranav's LGD ↔ SHRUG join is a **census-code** join. Our three sources are different: **none of
them carry a Census or LGD code — they are keyed only by free-text `(state, district)` name**, in
several vintages and spellings. So the crosswalk is fundamentally a **name-standardisation
problem**: resolve every source `(state, name)` to the current LGD `district_code`, then apply a
**population-weighted forward split** wherever a source unit is coarser than the current frame.

**Correction to an earlier assumption (verified here):** the planning notes recorded RBI
*Statement 4A* as "Census-2011 vintage (~765 districts)" needing wholesale apportionment. It is
actually **current-vintage** — 763 districts including post-2011 ones (Balod, Eluru, Hanumakonda,
Mulugu, even Manendragarh-Chirmiri-Bharatpur created 2022), 89 % matching LGD by name. So 4A needs
name-standardisation, not vintage apportionment — except for Delhi (see §4).

---

## 2 · The resolver (`resolve()` in build_crosswalk.py)

For each `(state, name)` it tries, in order:

1. **State normalisation** — `Orissa→Odisha`, `Pondicherry→Puducherry`, `NCT of Delhi→Delhi`,
   `A & N Islands→Andaman and Nicobar Islands`, etc.
2. **Candidate variants** of the district string — strips parenthetical / slash noise
   (`Baleshwar (Balasore)`, `Hathras / Mahamaya Nagar`), a trailing `" District"` token
   (`Gomati District`), a de-spaced form that repairs PDF-extraction artifacts
   (`Kancheepur am → kancheepuram`, `Bhubanesw ar → bhubaneswar`), **a camelCase / letter-digit
   splitter** (`SouthTwentyFourParganas → South Twenty Four Parganas`, `North24Parganas →
   North 24 Parganas` — SECC arrived camel-cased), and **a trailing `_XX` state-tag stripper**
   (`Bilaspur_Cht → Bilaspur`, `Aurangabad_Mh → Aurangabad`).
3. **Curated alias dictionary** (~330 entries) — renames (`Cuddapah→Y.S.R.`, `Gurgaon→Gurugram`,
   `Osmanabad→Dharashiv`, `Allahabad→Prayagraj`), spelling variants (`Hooghly`, `Kachchh`,
   `Khordha`), city→district for NMC college towns (`Bangalore→Bengaluru Urban`,
   `Calicut→Kozhikode`, `Bhilai→Durg`), and state-scoped entries for names that collide
   (`Bijapur`: Karnataka→Vijayapura vs Chhattisgarh→itself; Delhi's `West Delhi→West`).
4. **Exact match** on `(state, name)`, then **exact match on the all-India-unique census name**
   (this sidesteps the Telangana-under-Andhra reorganisation, same rule Pranav documented).
5. **Fuzzy fallback** — `rapidfuzz` token-sort ≥ 90 within state, ≥ 94 all-India-unique. Every
   fuzzy hit < 92 was hand-verified; all are legitimate spellings (`Nasik→Nashik`,
   `Jalor→Jalore`, `Kanyakumari→Kanniyakumari`).

**Result: 8,615 source rows resolved across 2,271 distinct district names.** The full resolved
mapping is `name_map.csv` — a **reusable gazetteer** the rest of the team can join on.

**Reusable one-call entry point.** The same engine is importable with no side effects:
`from build_crosswalk import crosswalk_file` puts *any* district-keyed file (a teammate's, in any
spelling/vintage) onto the 785 frame in a single call —
`crosswalk_file("mydata.csv", "State", "District", ["cases"], source="X")` — reusing every alias +
the fuzzy matcher + the population-weighted forward split. It only harmonises district → LGD (**not**
general cleaning: no type-fixing/dedup/imputation/units/outliers); ratios & per-capita must be passed
`extensive=False` so they are never split. See the function docstring for the options
(`apportion_undivided`, `lumped_states`, `absent_zero`).

---

## 3 · Handling missing data & vintage gaps (population-weighted forward split)

Two mechanisms put coarse/old source units onto the current frame. Both **preserve national
totals** (verified) and only ever split **extensive** quantities (counts, amounts); **ratios and
per-capita are recomputed** from the split parts, never split.

**(a) Post-2011 child districts** (`apportion()`). A current district created after 2011 has no row
in an older source. Using the family map (each current district → its 2011 parent census district,
from `view2`), when the 2011 **parent** is present but its post-2011 **children** are absent — i.e.
the source still lumps them — the parent's total is redistributed across parent + children by each
child's **2011 population** (summed from that district's 2011 sub-district populations in `view3`).
Where the source already lists children separately, nothing is touched.
*Example:* RHS-2019 sub-centres split Jaipur→{Jaipur, Dudu}, West Godavari→{West Godavari, Eluru}.

**(a2) Panel-aware split for RBI Statement 4A (2026-07-18 correctness pass).** 4A's district set
**tracks the Rajasthan/MP 2023 creations and Dec-2024 abolitions year by year**: Dudu has rows only
at Mar-2024/Mar-2025 (created Aug-2023, abolished Dec-2024, when its offices re-report under
Jaipur); Jodhpur (Gramin) is back inside Jodhpur's row by 2025-26; Neem Ka Thana, Gangapur City,
Kekri, Shahpura, Sanchore, Anupgarh likewise. The generic `apportion()` cannot handle this (it
skips any family with a present member), and before the fix the blank stub rows were summed into
**fabricated zeros** that blocked apportionment entirely — Jodhpur Rural's real ₹6,284 cr sat at 0
in every year, six districts had ₹0 deposits in the model, and CAGRs compared different territories
(Baksa −12 %/yr, Alwar −10 %/yr, both artifacts). `build_stmt4a()` now, per period: an absent
family member is **carved out of its head's lump at the member's own nearest observed year's
value** (real data beats a population share — Dudu's observed level is ~1 % of its family, its
population share ~7 %); only never-observed members split the remainder by 2011 population. Four
**multi-donor overrides** route carves the single-parent family map would mis-assign, with weights
read off the parents' own creation-drops/abolition-jumps (consistent on both events to within
organic growth): Gangapur City 75/25 Sawai Madhopur/Karauli; Neem Ka Thana ⅔/⅓ Sikar/Jhunjhunu;
Beawar 80/20 Ajmer/Pali; Kotputli-Behror 50/50 Alwar/Jaipur — plus Mahe and Yanam, whose rows the
2025-26 sheet folds into the PUDUCHERRY district row (Karaikal + Puducherry = the UT total to the
rupee). Two same-class name fixes: **"BENGALURU SOUTH" = Ramanagara renamed (2024)** — the old
alias sent its ₹20,972 cr into Bengaluru Urban and left Ramanagara blank — and **Vav-Tharad**
(Gujarat, Jan-2025, not in the LGD frame) folds back into Banas Kantha instead of being dropped
(₹2,419 cr recovered; 4A unmatched rows now 0). National totals conserved; every touched row is
flagged `is_apportioned`; residual caveat: multi-donor weights are read from noisy year-jumps, so
those districts' levels are estimates (their CAGRs are all sane 2–17 %/yr post-fix).

**(a3) Third correctness pass (2026-07-18): Warangal Urban = Hanumakonda + RHS recoveries.**
(i) **"Warangal Urban" (2016-2021) is the district renamed HANUMAKONDA in 2021** — the successor
holding the tri-city urban core (Kakatiya Medical College, the private-hospital cluster). The old
alias folded it into Warangal, which therefore carried BOTH districts across every source using
2016-21 names: RHS-2017/2019 facilities double-counted (215 SC), HMIS utilization volumes doubled
(OPD 3,257/1000 on Warangal's population alone), NFHS-5 reduced both districts to the urban+rural
mean, and PM-JAY handed Warangal all 118 empanelled hospitals while Hanumakonda sat at a fabricated
0 (83 of them — 55 private — are Warangal *Urban* registrations, now Hanumakonda's). Fix: GLOBAL
alias `warangal urban → hanumakonda` (`warangal rural → warangal` unchanged); each district now
carries its own surveyed/reported values in all four sources; only OSM/GHS-POP had been pinned
correctly before. Warangal's overall rank falls 69→177, Hanumakonda lands at 231 on real data, and
Mulugu (−88) sheds the inflated rates it had been *inheriting* from parent Warangal via the ladder.
(ii) **Single-district UTs in RHS 2016/2017/2019 label their one real district row "Total" /
"Total - District UT"** — the resolver's junk filter silently discarded them (never even logged):
Chandigarh (4 SC + 36 PHC + 10 HWC-PHC), D&N Haveli (47 SC + 24 HWC-SC), Lakshadweep (14 SC) were
dropped, then `fillna(0)` in the 2019 snapshot stamped fabricated zeros on top. Fix: `build_rhs()`
rewrites those district cells to the UT's own name before matching (+89 SC / +61 PHC / +7 CHC
recovered), and the 2019 `*_incl_hwc` columns now use `sum(min_count=1)` so the six districts
genuinely absent from the source (Mumbai Suburban, Bajali, Shi Yomi, EWKH, GPM, Leparada) stay NaN
→ imputation ladder instead of scoring "0 facilities". (iii) **Kalimpong's 2019 row leaves
Sub-Centres blank while Darjeeling's 230 equals the undivided 2012 total to the digit** (198.66 +
31.34) — the one partially-blank row in the whole panel; the SC lump is carved back by the same
2011-population shares (Kalimpong ≈31.3, growth −80.5 % → +6.6 %). (iv) `facility_growth` now
pivots without re-fabricating 0 for all-NaN district-years (`pivot`, not `pivot_table+sum` — the
min_count lesson applies to *every* aggregation step), and `redistribute_lumped_state()` refuses
to spread a lump when the state has no data at all (latent `crosswalk_file(lumped_states=…)` trap).

**(a4) Fourth correctness pass (2026-07-18): undivided "Jaintia Hills" + split-family weight
repairs.** (i) **Bare "Jaintia Hills" is the UNDIVIDED pre-2012 district** in RHS 2006/2010/2012
and all 7 SECC-2011 tables — but the alias routed it to **East** Jaintia Hills, the *small 2012
carve-out* (2011 pop 122k of 395k), handing East the whole undivided district (74 SC in 2012, the
full SECC household base) while **West** Jaintia Hills — the successor holding HQ Jowai — fell to
the imputation ladder. Fix: the alias now targets the family **head** (`jaintia hills → west
jaintia hills`, the same head-targeting pattern that makes `burdwan → purba bardhaman` correct),
letting `apportion()` split by 2011 population; the one source where the bare name genuinely means
East — RHS-2016, which lists "West Jaintia Hills" separately in the same file — is pinned inside
`build_rhs()`. West's rank falls 405→536 as the model finally sees its real (deprived) SECC data
instead of Meghalaya state medians. (ii) **Three split families' apportionment weights were skewed
by the view3 tehsil join losing member-specific territory** (99999 urban buckets / unmatched
tehsils), distorting the *ratio*, not just the level: Jaintia split 53/47 instead of the true
**69/31**, Kancheepuram|Chengalpattu 41/59 instead of **36/64** (this pair also sets the GHS-POP
`PAIR_FIX` shares → the model's per-capita denominators), Tirap|Longding **70/30 instead of
46/54**. All three are now in `SPLIT_FAMILY_SHARES` with published Census-2011 member populations
that reconcile against SHRUG's own undivided totals (Tirap and Kancheepuram to the person, Jaintia
within the −0.6 % DCHB vintage gap, same as Tripura). (iii) **The two worst residual families were
closed in v1.4.9 (2026-07-19)** with citable member figures, both now in `SPLIT_FAMILY_SHARES`:
**Aizawl|Saitual** — the tehsil join had collapsed the family to 69,507 vs census 400,309 (Aizawl's
urban core lost), splitting ~54/46 when the truth is ~92/8; Saitual received ~46 % of the family's
HMIS utilization on ~8 % of its people and, divided by its own (correct) GHS-2025 denominator,
scored IPD 569/1000. Member figure: Saitual **32,033** = ORGI/Delimitation-derived 2011 population
in current boundaries (Ngopa RD block 18,730 + Phullen 13,303; citypopulation.de
/en/india/admin/mizoram/794__saitual), Aizawl = 400,309 − 32,033. Documented approximation: the
figure includes Champhai-origin Ngopa (+18.7k) but excludes the Saitual-town slice of
2011-Thingsulthliah (−11.6k+), partially offsetting; the frame is single-parent (Saitual→Aizawl) so
a finer two-parent split has nowhere to live. Post-fix Saitual IPD 99/1000, lab-tests 10,853→1,884;
ranks Saitual 149→315, Aizawl 419→282 (the capital again outranks its carve-out).
**Baksa|Tamulpur** — family sum 511,708 vs undivided 950,075, split 54/46; Wikipedia's Baksa
demographics publish residual Baksa **560,925** after the Tamulpur carve ⇒ Tamulpur **389,150**
(Tamulpur + Goreswar ACs), reconciling to SHRUG's undivided total *to the person*; true split
59/41 (small ratio correction, large level restore). Verification that the defect was
family-specific: **Lunglei|Hnahthial reconciles exactly** (135,315 + 26,113 = 161,428 = census).
**Still documented, not fixed:** **Champhai|Khawzawl** (~2pp ratio error once Ngopa's transfer to
Saitual district is accounted: family sum 101,108 vs 125,745 − 18,730 = 107,015 — below
materiality), and the Arunachal West Siang/East Siang/Lower Subansiri clusters and multi-parent
Telangana/Rajasthan families flagged in §3(d) — for those the published member sums do NOT
reconcile to one parent (territory moved across families), so a single-family share table cannot
represent them.

**(b) Whole-state lumps** (`redistribute_lumped_state()`). RBI Statement 4A reports **all of Delhi
as a single "New Delhi" row** (₹22.39 lakh cr = the entire NCT). That lump is spread across all 11
Delhi LGD districts by 2011 population, so no Delhi district is blank and none carries the whole-NCT
figure. Delhi was the only true single-lump case; Arunachal (18/25) and Nagaland (12/16) simply
have remote/new districts RBI does **not** report separately — those are left honestly missing
(negligible banking), not fabricated.

**(c) `pop2011` repair for 5 code-less post-2011 districts (`POP_FAMILY_FIX`).** Four Tripura
children (Khowai, Sepahijala, Gomati, Unakoti) and Assam's **Bajali** carry LGD sub-district census
codes of `00000`, so their 2011 sub-district PCA population never joins `view3` **and** the
parent-map inference fails. Untreated, each child's `pop2011` fell to **0** (blank per-capita rates
for *every* metric) while its retained 2011 parent kept the **whole undivided total** (West/South/
North Tripura counting their carved-out children's people). Fix, applied once at the `POP` layer in
`build_crosswalk.py` so it cascades to `consolidated.pop2011`, RBI per-capita, and every harmoniser's
per-1,000 rates: reallocate each 2011 parent's population across parent + children by **published
Census-2011 district populations used as shares** (DCHB via *List of districts of Tripura*), rescaled
so each family sums **exactly** to the parent's frame total — West family already summed to 1,725,739
to the person; South/North rescale ×0.979 / ×0.971. Tripura's 8-district total stays 3,673,917 to the
person. Bajali is the **residual** of old undivided Barpeta (SHRUG pc11 dist 303 = 1,693,622) minus
current Barpeta (1,439,806) = **253,816** — a genuine undercount restored (national `pop2011` +253,816,
its only change). No district now has `pop2011 = 0`.

**(d) `pop2011` urban-core / unmatched-tehsil repair for districts that did NOT lose territory.**
SHRUG parks each district's unassigned urban population in a `99999` sub-district with **no LGD
sub-district to join to**, and a few named tehsils don't join either, so the `view3` tehsil sum
silently drops them — worst for metros (Bengaluru Urban lost **8.44M of 9.62M**, Chennai 1.94M,
Mumbai, the whole Kolkata metro, Thiruvallur, Tiruppur). This deflated the per-capita denominator and
*inflated* per-capita rates for exactly the biggest markets. Fix at the `POP` layer: the district-level
2011 PCA total (`view2.pc11_pca_tot_p` == the SHRUG district total) is the complete, authoritative
figure and is **safe for any established district that is NOT a 2011 parent of a post-2011 child** — it
kept its whole 2011 territory, so that total is its current total and cannot double-count. Parents are
detected from `view3` itself (the 2011 census districts that fed tehsils to any post-2011 district).
Districts that **did** lose area are skipped — including via a **multi-parent child assigned to another
family** (Sikar contributed to Neem Ka Thana, which the parent-map placed in Jhunjhunu's family, so a
naïve "un-split" test would wrongly lift Sikar to the old undivided total and double-count). This lifts
**~54 districts** onto their exact SHRUG district total (Bengaluru → 9,621,551, Chennai → 4,646,732).
National `pop2011` rises from 1,171.8M to **1,208.1M** and provably stays **≤ the Census-2011 total**
(no double-count — verified: no district exceeds its own district PCA).

**(e) `pop2011` split-family repair (West Bengal).** Four 2011 parents split into a rural retained
parent + a district holding the urban core; the whole family shortfall is that one `99999` lump, which
belongs to a *specific* member (Asansol-Durgapur → Paschim Bardhaman, Siliguri → Darjeeling), so
proportional-by-population would hand the city to the rural sibling. These four use **published
Census-2011 district populations** (DCHB via *List of districts of West Bengal*), anchored to the
retained parent's SHRUG total: Purba/Paschim Bardhaman (4,835,532 / 2,882,031), Darjeeling/Kalimpong,
Paschim Medinipur/Jhargram, Jalpaiguri/Alipurduar — reconciling to the parent total (three to the
person, one within 948).

*Documented residual (left indicative, ranks hold):* (1) **23 other split families (~6.8M)** —
mostly the newest **AP/Telangana/Rajasthan** reorganizations whose children are drawn from **2+ old
districts** (multi-parent); their shortfall is distributed unmatched tehsils with no clean 2011
retabulation to key on, so filling them can't be done safely without tehsil-level attribution.
(2) **Lost-territory established districts** skipped by (d) (Sikar, and other minority parents of
multi-parent children) stay at their matched-tehsil sum. (3) A handful of **pre-existing `view3`
over-matches** for tiny metro districts (New Delhi, North/Central Delhi, South Goa, Mandi) where LGD
tehsils matched larger SHRUG sub-districts — a frame-join artifact independent of this repair.

**NMC tier-2 manual placement (`nmc_manual_placements.csv`).** 50 colleges (6,492 seats) had a
District cell that was either a bare **metro label** (`Delhi`, `Navi Mumbai`, `Imphal` — one label
spanning several districts) or a **truncated / garbled PDF locality** no alias/fuzzy rule could
safely reach (`Perintalman na`, `Bhadradri Ko`, `Peerancher u`, `eTguem, Tkuumrkur`…). Each was
hand-placed **by the institution's known campus locality**, keyed on the college name (verified to
match exactly one college, and never a college that was already placed): AIIMS/Safdarjung → South
Delhi, Maulana Azad → Central, UCMS-GTB → Shahdara, Rohini → North West; Terna/DY-Patil (Nerul) →
Thane, MGM (Kamothe) → Raigad; RIMS → Imphal West, JNIMS (Porompet) → Imphal East; peri-Hyderabad
colleges → Ranga Reddy / Medchal-Malkajgiri; and each named town to its single district. **Result:
all 118,190 MBBS seats placed (100 %)** — every placement is logged with its original label for audit.

**NMC PG placement (`nmc_pg_by_district.csv`, `nmc_pg_manual_placements.csv`).** The PG seat matrix
has **no District column** — only (State, College, Course, Seats). Each of the 572 PG colleges is
placed by a 4-step cascade: (1) a **tier-2 override** for the 34 **PG-only institutes** (standalone
cancer / cardiac / mental-health / kidney / defence-services hospitals that teach no MBBS, so are
absent from the UG file — Tata Memorial, NIMHANS, Kidwai, Sri Jayadeva, SGPGI, IHBAS, CNCI, INHS
Asvini…); (2) **exact** then (3) **fuzzy** college-name join to the UG matrix (which already carries
a resolved district — the same institution's MBBS + PG wings collapse to one district); (4) a
**comma-town fallback** through the standard LGD resolver (e.g. `…, Bhubaneswar` → Khordha,
`…, Tezpur` → Sonitpur). Method mix: ug_exact 462 · town 61 · override 34 · ug_fuzzy 13. The source
state column is first repaired for PDF space-splits (`Maharashtr a`, `Chhattisgar h`, `Uttarakhan d`)
and spellings (`Rajisthan`, `Tamilnadu`). **Result: 100 % of 54,855 PG seats placed across 287
districts.** A `pg_superspecialty_seats` column isolates degree-prefixed **DM/MCh** (3,347 seats) as a
tertiary/referral-depth signal — it concentrates in the true hubs (Chennai 309, Bengaluru Urban 285,
Mumbai 180, Lucknow 178). No apportionment (place-specific, like UG; absent district = 0).

**What stays unmatched (documented in `unmatched_report.csv`):**
- **Districts newer than the LGD snapshot** — Vav-Tharad (GJ 2023), Keyi Panyor/Bichom (AR 2024),
  Meluri (NL 2024), Kushavati (GA), Polavaram (AP). No LGD home yet → cannot be placed. (These affect
  RBI/RHS rows, not NMC — no medical college sits in one.)

---

## 4 · Reconciliation (all verified against source totals)

| Source | Crosswalk total | Source / official total | Match |
|---|---|---|---|
| RBI Statement 4A deposits (2025-26) | ₹2,60,74,770 cr | ₹2,60,76,266 cr (4A ALL-INDIA) | 99.99 % |
| Implied national CD ratio | **81.8 %** | ≈ 82 % (reality) | ✓ |
| RBI Bank Outlets | 169,169 | 169,300 (source) | 99.9 % |
| RHS-2019 sub-centres (+HWC) | 159,741→160,696 | 160,713 (district rows incl. the 3 single-district-UT rows, §3(a3)) | ✓ |
| NMC MBBS seats placed | 118,190 | 118,190 | 100 % (50 colleges hand-placed, §3) |
| NMC PG seats placed | 54,855 | 54,855 | 100 % (34 PG-only institutes hand-placed, §3) |

Gaps are the handful of brand-new districts absent from source, not join errors.

---

## 5 · Output files

All files below live in `data/processed/crosswalk/`.

| File | Rows | What it is |
|---|---|---|
| `consolidated_by_district.csv` | 785 × 31 | **CONSOLIDATED** headline table — health infra, MBBS + PG seats, bank outlets, deposits/credit/CD/per-capita/CAGR, all on the current frame |
| `name_map.csv` | 9,180 | reusable `(source, state, name) → district_code` gazetteer (+ method, score) |
| `unmatched_report.csv` | ~12 | every unresolved / ambiguous row, with reason, best-fuzzy guess & value |
| `rhs_health_panel.csv` | 785 × 6 yrs | RHS facilities long panel (2006-2019) + `is_apportioned` |
| `rhs_health_2019.csv` | 785 | RHS latest snapshot (SC/PHC incl. HWC folded in per the comparability note) |
| `nmc_ug_by_district.csv` | 785 | MBBS colleges + seats per district (absent = 0 colleges) |
| `nmc_pg_by_district.csv` | 785 | PG seats + college count + super-specialty (DM/MCh) seats per district (absent = 0) |
| `nmc_manual_placements.csv` | 50 | tier-2 hand-placed UG colleges: original label → assigned LGD district + seats (audit trail) |
| `nmc_pg_manual_placements.csv` | 34 | tier-2 hand-placed PG-only institutes → assigned LGD district + seats (audit trail) |
| `rbi_bank_outlets.csv` | 785 | bank outlets by bank-group & population-group |
| `rbi_deposits_credit_panel.csv` | 785 × 4 periods | Statement 4A long panel + CD ratio + per-capita |
| `rbi_deposits_credit_latest.csv` | 785 | 4A latest + 3-yr deposit/credit CAGR |
| `slbc_mp.csv` / `nabard_wb.csv` | 55 / 23 | single-state supplements, LGD-keyed |
| `public_health_infra_estimated_by_district.csv` | 785 | **MODELLED ESTIMATE, not observed data** — see §5c before using |

**Caveats:** (1) `pop2011` and per-capita denominators are 2011-vintage (the only all-India district
population we hold) — treat per-capita as indicative, levels/ranks as solid. (2) All 118,190 MBBS UG
seats **and** all 54,855 PG seats are now placed on the frame (100 % each; PG via the college-name
join + comma-town fallback + a 34-institute override, §3). (3) The remaining honest coverage gaps are
6 low-banking NE districts with no separate RBI reporting (left NaN, not zeroed) and a few 2023-24
districts newer than the LGD snapshot — both listed in the unmatched report.

## 5b · Teammate-source harmonised files (via `scripts/harmonise_team_sources.py`)

Already-pulled teammate datasets, placed on the same 785 frame. Extensive counts are apportioned to
post-2011 children; percentages are recomputed from the split parts (share of each file's base total),
never split directly. All reconcile to **100 %** of their district-row source totals except HMIS
(99.7 %: one AP row has a corrupted district name — `�SR.`, a mojibake — plus a few current districts
are simply absent from the HMIS list).

| File | Rows | What it is |
|---|---|---|
| `janaushadhi_by_district.csv` | 785 | # Jan Aushadhi (PMBJK) kendras per district — new affordable-medicine channel signal |
| `hmis_facilities_by_district.csv` | 785 | HMIS facility counts (SC/PHC/CHC/SDH/DH/med-college) — **duplicates our RHS row**, kept for completeness |
| `secc_income_bands_by_district.csv` | 785 | SECC-2011 household income bands (<5k / 5-10k / >10k) |
| `secc_deprivation_by_district.csv` | 785 | SECC-2011 deprivation indicators (D1–D7 + no-deprivation) |
| `secc_education_by_district.csv` / `secc_education_urban_by_district.csv` | 785 | SECC-2011 education profile (total / urban) |
| `secc_income_source_by_district.csv` / `secc_income_source_urban_by_district.csv` | 785 | SECC-2011 main income source / urban occupation |
| `secc_land_ownership_by_district.csv` | 785 | SECC-2011 land ownership & irrigation |
| `name_map_team_sources.csv` | 5,874 | resolved `(source, state, name) → code` gazetteer for these sources |
| `unmatched_team_sources.csv` | 1 | the single unresolved row (the `�SR.` mojibake) |

All are Siddharth's pulls. **Caveats:** (1) the standalone SECC here **duplicates SHRUG's `secc_*`
modules** already on the frame (`district_frame/`) but is more granular — pick one per feature.
(2) HMIS facility counts **duplicate our RHS panel** — prefer `rhs_health_*` for infra.
(3) State-level PM-JAY and public-health-infrastructure pulls are **not** harmonised (state
granularity can't populate 785 districts without fabricating) — see §5c for
public-health-infrastructure's one deliberate exception.

**Correctness pass (2026-07-18):** (i) source **percentages are never apportioned** — the ext-col
filter now excludes `*_Pct` as well as `Pct_*`, which removes `NoDeprivation_Pct` (it had been
population-scaled as if it were a count, corrupting all 244 apportioned rows); its correctly
recomputed twin `Pct_NoDeprivation_HHs` carries the same definition (verified = NoDeprivation_HHs
/ Total_Households, matching the source). All recomputed `Pct_*` columns were verified against the
source's own definitions (all bases confirmed: households for income/deprivation/land, population
for education/urban-occupation). (ii) The shared `to_spine()` engine now sums with `min_count=1`:
a matched district whose source cells are all blank stays **NaN ("reported nothing")** instead of
a fabricated 0 — `absent_zero=True` remains the only opt-in to zero. This flipped 1,686
blank-sourced cells in `hmis_facilities_by_district.csv` from 0 to NaN (e.g. 424 districts' blank
`StandaloneDistrictHospital`). (iii) These files also picked up engine improvements that postdated
their first build (WB split-family population shares, Sikkim rename aliases, the MH
Raigarh→Raigad pin) — totals still reconcile 100.00 %.

## 5c · Modelled ESTIMATE — public_health_infrastructure by district (NOT harmonised data)

**Team decision (2026-07):** unlike everything else in this folder, `public_health_infra_estimated_by_district.csv`
is not real district-level data — it's a **facility-count-weighted apportionment** of the
state-level doctors/paramedical/specialists/beds/vacancy pulls, built by
`scripts/apportion_public_health_infra.py`. We agreed to build this (the team was asked
explicitly, given we'd skipped the equivalent move for PM-JAY) **on the condition that it stays
walled off** from the real data — it is never joined into `consolidated_by_district.csv`, and
every row is flagged `is_estimated_from_state_total = True`.

**Method:** `district_value = state_value × (district_facility_count / state_facility_count)` —
e.g. a district's share of its state's DH-doctor total is proportional to that district's count
of District Hospitals (from our own `rhs_health_2019.csv` counts, already on this frame). Every
column reconciles to exactly 100% of its source state total by construction. The five state-level
source files themselves live in `_deprecated/public_health_infrastructure/` (retired 2026-07-15 as
state-resolution-only — see `_deprecated/README.md`); `apportion_public_health_infra.py` is the one
sanctioned reader of a retired source.

**Read the caveats in the script's docstring before using this for anything beyond a rough
choropleth.** In short: (1) it assumes the per-facility rate is uniform across a state's
districts — known to be **wrong for vacancy specifically**, since remote/rural districts run
higher vacancy in reality than this method can recover; treat `vacancy_*` columns as the least
reliable. (2) `weight_fallback_*` columns flag districts in states where the relevant facility
count was zero (fell back to an equal split instead — e.g. `sub_divisional_hospitals` is 0 for
11 states, so `*_sdh_*` columns fall back for those states' districts). (3) The 2018/2019
doctors/paramedical/specialists sources predate the J&K/Ladakh split — their "Jammu and Kashmir"
state total is spread across *both* current J&K and Ladakh districts (the historical undivided
state covered both), which is a modelling choice, not a fact. (4) because each column is just
`facility_count × state-average-per-facility`, it carries **no within-state district signal**
beyond the facility counts already in `consolidated_by_district.csv` — its only genuine
information is the *cross-state* per-facility rate. Do **not** feed it into an index alongside the
raw facility counts (that double-weights the same variable).

**If you need this data reliably, don't use this file as-is** — it's a stopgap, not a source.

## 5d · OSM healthcare POIs (via `scripts/harmonise_osm_pois.py`) — first Novel/proxy source

`osm_pois_by_district.csv` (785 rows): OpenStreetMap counts of **hospitals (55,952),
clinics/doctors (26,560), retail pharmacies (7,888) and diagnostic labs incl. chain
sample-collection points (1,268)** — pulled 2026-07-16 nationwide via Overpass
(`data/raw/osm_pois/fetch_osm_pois.py`, manifest + raw TSVs alongside; OSM © contributors, ODbL).
This is the **current-vintage private-supply signal** that replaces the 2011 towns-directory
column in the model (Novel Tier-1 #1 in `docs/proxy_data_plan.md`).

Unlike everything else in this folder the source is **coordinate-keyed, not name-keyed**: POIs are
point-in-polygon'd onto geoBoundaries-2021 ADM2 district polygons (735 districts, themselves
LGD-sourced; state assigned via the ADM1 layer because ADM2 carries no state attribute and Indian
district names collide across states), and only then do the polygon *names* go through the standard
resolver. Ten geoBoundaries name variants were added to the curated alias dict — most importantly
`Warangal (U)` → **Hanumakonda** (unaliased it fuzzy-landed on Warangal, double-counting it) — and
two polygon-gap districts (Bajali, Eastern West Khasi Hills) are population-split from their donor
districts in the harmoniser. **All four categories reconcile to 100.00%** of the placed POIs;
774/785 districts carry ≥1 POI (the 11 zeros are remote NE/island districts with genuinely nothing
mapped); 94 rows are apportioned. Gazetteer: `name_map_osm_pois.csv`.

**Caveat (repeat of the registry rationale):** OSM completeness is urban-biased and category-skewed
(hospitals well-mapped, pharmacies ~8k of a ~0.9M real-world universe) — the columns are
**presence/density signals for percentile scoring**, never universe counts.

## 5e · PM-JAY empanelled hospitals (via `scripts/harmonise_pmjay.py`) — Novel/proxy Tier-1 #2

`pmjay_hospitals_by_district.csv` (785 rows): **31,924 PM-JAY-platform empanelled hospitals**
(14,407 private / 17,445 public) from the NHA hospital search's no-login Excel export, pulled
2026-07-16 (`data/raw/pmjay_hospitals/fetch_pmjay_hospitals.py` + manifest + the 13 MB .xls).
This is the **insured cashless-care supply signal** — and the district-level rescue of the
PM-JAY row (scheme utilization is only public at state level).

Scope: everything approved on the PM-JAY IT platform **except** "Only CGHS"/"Only CAPF"
(central-employee schemes, 3,150 rows); "State Specific Empanelment" extensions stay in.
208 `-NA-`-district rows dropped: legacy pre-bifurcation "ANDHRA PRADESH" registrations of
Hyderabad hospitals, 79 of them exact name-duplicates of properly-tagged Telangana rows —
counting them anywhere would double-count Hyderabad. District names are **current-vintage**
(Gaurella Pendra Marwahi, Soreng, all 11 Delhi districts appear by name), so this was a pure
name-standardisation job: 11 portal variants added to the curated aliases (Sikkim's four
pre-2021 names → Gangtok/Mangan/Namchi/Gyalshing — their carved-out children Pakyong & Soreng
have own rows, so plain renames, no split; `Raebareli`, `East/West Midnapur`, `Nellor`,
`Ananthpur`, `North Cachar Hill`, `Bandipur`). **All three columns reconcile to 100.00%**;
750/785 districts have ≥1 empanelled hospital; 61 rows apportioned. Gazetteer: `name_map_pmjay.csv`.

**Coverage caveat (documented, not imputed):** the portal covers the PM-JAY platform only.
**West Bengal** (runs Swasthya Sathi outside PM-JAY) and recent joiners **Odisha & Delhi** are
structurally understated — the 35 zero districts are all WB/Odisha and reflect true scheme
absence, not missing data. The `pmjay_private_share` feature is NaN there and handled by the
model's imputation ladder (audited in `coverage_report.csv`).

## 5f · Current district population — GHS-POP (via `scripts/harmonise_current_pop.py`) — Novel/proxy Tier-1 #3

`ghs_pop_by_district.csv` (785 rows): **pop_ghs_2015 / pop_ghs_2025** from JRC **GHS-POP
R2023A** ~1km rasters (14 tiles per epoch in `data/raw/ghs_pop/`, CC BY 4.0), zonal-summed
onto the same geoBoundaries-2021 polygons as §5d via `rasterio.features.rasterize` +
`np.bincount`. **This is now the model's per-capita denominator** (every `*_per100k`
feature) and the source of the `pop_growth_15_25` demographic-momentum feature.
100.00% of assigned population reconciles both epochs; national 2025 = 1,453.7M;
96 rows apportioned; every district > 0 (asserted — these are denominators).

**WorldPop was pulled first and REJECTED** (the full Tier-1 #3 story): the WorldPop
R2025A constrained India raster failed census-2011 validation — metro cores at
0.34–0.62× their own **2011** counts (Mumbai Suburban 3.2M vs 9.4M, Chennai 0.40×,
Kolkata 0.41×) with neighbours correspondingly inflated; 135/785 districts implausible.
An independent `rasterio.mask` computation reproduced the sums, proving the raster (not
our zonal code) wrong. Evidence kept in `_deprecated/worldpop_r2025a/` (the ~16MB rasters
themselves were deleted 2026-07-17; `fetch_worldpop.py` there re-downloads if needed). The
harmoniser now carries an **automatic validation gate** (national 2025/2011 ratio in
[1.05, 1.35]; < 40 districts outside [0.7, 2.2]) so a bad raster can never silently
become a denominator. GHS-POP passes: national 1.203, 16/785 outside (nearly all tiny
NE/enclave districts).

Two data repairs surfaced by the validation, worth quoting in the methodology note:
- **The remaining ratio "outliers" mostly indict `pop2011`, not GHS-POP**: master pop2011
  holds Aizawl at 37k (census: 400k), Sikar at 1.69M (census: 2.68M), and Delhi's
  pre-2012 district layout — per-capita features silently inflated/deflated there in
  v1–v1.2. The denominator switch fixes them.
- **geoBoundaries mis-draws the 2019 Kancheepuram/Chengalpattu split line** (polygon
  areas 2,718/1,740 km² vs real 1,656/2,945): pair total is right, so the harmoniser
  re-splits the pair by 2011 population shares (both flagged `is_apportioned`).

Boundary handling: GHS tiles cover neighbouring countries, so unassigned populated cells
are **dropped, never snapped** (they are foreign); the near-boundary sliver within one
cell of an India polygon is 0.56–0.59% and reported by the script.

**State raking — Kerala & Tamil Nadu (added v1.4.5, 2026-07-18; §3b in the harmoniser).**
GHS-POP disaggregates the national total by *built-up surface*, so states with continuous
"rurban" settlement absorb too much and come out overstated — worst: **Kerala +28%** (its raw
GHS growth 2015→25 is an impossible +22% for a near-static state) and **Tamil Nadu +9%**.
Because `pop_ghs_2025` is the denominator of every per-capita feature, that overstatement
*deflated* those states' per-capita rates. The census-2011 gate can't catch it (their 2011
census is real, so the ratio just looks high). Fix: multiply each district in those two states
by *(official state total / GHS state sum)*, **per epoch**, preserving GHS's within-state
spatial detail and re-anchoring only the marginal. Targets are the **NCP-2020 (MoHFW)
"Population Projections for India and States 2011–2036"** 5-year series, linear-interpolated to
the GHS epochs (Kerala 2015/2025 = 34.34/36.06 M ⇒ ×0.917/×0.787; TN = 74.14/77.32 M ⇒
×0.950/×0.921). Per-epoch is deliberate — it fixes both the level and Kerala's fake +22% growth
(→ +5.0%). After the rake the national total is 1,437.4M and the validation gate still passes
(1.188, 14/785 outside). `RAKE_TARGETS` is a one-line-extensible dict. Effect on ranks:
overall ρ=0.9999, Kerala districts +12 ranks mean (Malappuram +31), no other-state district >10.

**Karnataka added v1.4.6 (2026-07-18).** The candidate flagged above is now raked: the NCP
series was verified directly against the report PDF (pp. 262–263; its Kerala/TN rows reproduce
the v1.4.5 targets exactly) — Karnataka 61,095 / 64,229 / 66,845 / 68,962 thousand for
2011/16/21/26, interpolated to the GHS epochs = 63.60/68.54 M ⇒ **×0.957/×0.930** (GHS had
Karnataka **+7.5%** overstated, with +10.9% implied 2015→25 growth vs NCP's +7.8%). After this
rake the harmonised national total is 1,432.2 M and the validation gate still passes (1.184,
14/785 outside). Karnataka districts rose a mean **+6 ranks** (Mysuru 25→21, Bengaluru Urban
82→76). The remaining major states sit within ±3.5% of NCP and stay unraked.

## 5g · VAHAN vehicle registrations (via `scripts/harmonise_vahan.py`) — Novel/proxy Tier-1 #4

`vahan_by_district.csv` (785 rows): **veh_reg_2019 / veh_reg_2023** (all-category calendar-year
registrations), **veh_2w_2023 / veh_lmv_2023** (two-wheeler and car split), `n_vahan_offices`.
Source: the India Data Portal (ISB) CKAN mirror of the official MoRTH VAHAN dashboard —
RTO × month × vehicle category, 2019-01→2024-05, 1,314 offices (raw pull + re-pull script in
`data/raw/vahan/`). The dashboard itself is a JSF app; the mirror is the same data flattened.

**Cleaning (the mirror carries scrape-glitch rows).** Some rows hold a NATIONAL or STATE
total instead of the office's own count (e.g. 18,623,048 replicated across ~18 unrelated
offices in one 2020 month; "Lower Siang" out-registering all of Arunachal ×700). Six
deterministic rules (S1 replicated-total clusters at counter-jitter tolerance; S2
fitness/testing-centre pseudo-offices; S3 per-office-category q25 spike screen; S4 hard cap;
S5 state-consistency backstop with a 2019 specialisation exemption — DL51 Burari is genuinely
Delhi's goods-vehicle authority; **S6 spike-and-revert**) drop 634M of fake volume,
calibrated so 2019 — verified dump-free — has **zero false positives**.

S6 exists because S1–S5 are all *row*- or *state*-local and two dump shapes evade them:
a **state** total dumped into an office of that same state (S5's ×10-rest-of-state test
fails when the rest of the state is comparable — Unakoti out-registers Agartala 3:1 in
2022-23), and **residue in rare categories** whose national totals are small enough to slip
under S1's 9,000 floor (Lower Siang's every-row-is-a-dump years still left ~3.5k/yr ≈ ×9 its
real ~400/yr). S6 works at office-year level: >5× the office's own 2019 baseline **and**
back to that baseline by 2024 = a transient dump, because real new/expanding RTOs *sustain*
their volume (Jaipur-Second is ×80 its 2019 in 2023 and still ×116 in 2024 — left alone).
It flags 14 offices, 12 of them 2020 dump targets S1 had only partly stripped; the
smoking gun is that six unrelated Tamil Nadu offices plus one in AP share a byte-identical
2020 total of 87,881 (AP327 at 87,880 — counter jitter), and their raw 2020 sums are all
18,623,0xx. Only Lower Siang and Unakoti are touched in a modelled year; 2019 is untouched.

**Anchor gate** (script refuses to write): cleaned CY totals must match FADA/VAHAN
ex-Telangana bands — we land 24.65M (2019) / 24.01M (2023) vs published ~24.7M / ~24.5M;
state totals match (UP 3.44M, MH 2.56M, GJ 1.82M). Independent cross-check: the dumped
values *are* the true national totals, and our cleaned CY totals reproduce them from the
bottom up — 18.87M vs the dumped 18,623,048 (CY2020, 1.3%) and 24.01M vs 23,997,339
(CY2023, 0.05%).

**Office → district (1,307 of 1,312 real offices placed, 99.94% of cleaned volume):**
curated overrides for ~145 offices (metro locality RTOs, renamed towns, post-2022 AP splits,
HP RLA towns — keyed by stable office code) → LGD district-name resolution on normalised
name candidates → exact then fuzzy (≥90, unique-in-state) lookup against the view3
sub-district gazetteer (RTO towns are usually tehsil HQs). All fuzzy hits hand-audited; fixes
worth noting: Itchapuram ≠ Pithapuram (fuzzy override → Srikakulam); AS-01 "Kamrup" is the
Guwahati office → Kamrup Metro (not rural Kamrup); KA-04 "Bengaluru North" is a city RTO →
Bengaluru Urban; MH-02 "Mumbai (West)" is Andheri → Mumbai Suburban. 5 offices left unplaced
on purpose (state STAs / head offices, two ambiguous small towns; 0.06% of volume) —
`unmatched_vahan.csv`; full office provenance in `name_map_vahan.csv` (1,307 rows).

**Missingness semantics: NaN, not zero.** 69 districts have no VAHAN office (their residents
register in a neighbouring district) → NaN, so the model's ladder imputes parent→state.
**Telangana (33 districts) and Lakshadweep were not on VAHAN in this window at all** —
structural gap, documented, NaN → national median. A year is zero-filled **only** where the
district's offices actually reported that year (then a missing category really is none
sold); an office the mirror simply stops carrying is missingness, not zero — Dhemaji has 96
rows in 2019 and silence after, and fabricating a 0 there would have ranked a real market
bottom on both level *and* growth. Same for the office-years S6 voids. Net: 713/785
districts observed for CY2023 (72 NaN).

Reconciliation: 100.00% of cleaned+placed volume lands on the frame; top districts
face-check as the real registration markets (Bengaluru Urban 693k, Pune 505k, Ahmedabad
328k, Chennai 294k in 2023), and per-capita the top district is Gurugram (78 per 1,000).
Model layer computes growth only where the 2019 base ≥ 500 (a new RTO opening mid-window
shifts volume between districts and fakes growth) and the four-wheeler share only where the
district registers ≥ 200 personal vehicles a year (below that the mix is noise: 3 cars and
0 bikes is not "100% premium").

## 5h · HMIS monthly seasonality (via `scripts/harmonise_hmis_seasonality.py`) — v1.4.10

`hmis_seasonality_by_district.csv` (785 rows): **acute_peak_quarter_share** (share of the
FY-2018-19 year's episodic acute caseload landing in the district's peak cyclic 3-month
window; 25% = flat year), **acute_peak_quarter** (window label, e.g. "Jul-Sep" — for the PPT
seasonal-planning visual), `acute_basket_annual`, `opd_months_reported`,
`hmis_seasonality_basis`.

Source: the same HMIS-portal `2018-2019.zip` that closed the MP gap (v1.4.8), but the
**A.Monthwise** tree — 36 states × 12 per-month SAS-HTML files, all districts, already on
disk (git-ignored; re-downloadable). `data/raw/data_hmis/parse_monthly_seasonality.py`
extracts the **11 episodic items behind the acute index's own level features** (malaria +
dengue positives, typhoid + diarrhoea/dehydration IPD, child diarrhoea + URI, snakebite)
plus OPD-total as reporting guard → committed `hmis_monthly_acute_2018_19.csv` (8,448 rows,
704 districts; per-state×item×month reconciliation vs the files' own state-total columns:
**5,183/5,183 OK**).

The harmoniser crosswalks the monthly **counts** extensively (Brihan Mumbai split, family
apportionment — doctrine 3: never apportion a ratio) and only then computes the share.
Guards: OPD reported ≥11/12 months AND basket ≥120 annual cases, else NaN → ladder
(observed 777, low_volume 3, absent 5). Face check: peak windows are monsoon-centred
(Jul-Sep 151 districts, May-Jul 140, Jun-Aug 129); the spikiest district at real volume is
**Bareilly (63%, Aug-Oct, 29k annual cases) — the documented 2018 western-UP
malaria/dengue outbreak**, i.e. the metric prices exactly the surge geography it should.
Caveat: single-FY estimate, read as a relative percentile, never an absolute forecast.

## 5i · Census 2011 D-02 in-migration (via `scripts/harmonise_census_migration.py`) — v1.4.10

`census_migration_by_district.csv` (785 rows): **mig_recent_in** (in-migrants whose last
residence was OUTSIDE the district — other districts of the state + other states + abroad —
duration <10 years at census), **mig_total_in** (same origins, all durations).
Intra-district moves ("elsewhere in the district", mostly rural→urban + marriage migration)
are deliberately excluded — not a floating-population signal.

Source: official Census NADA catalog (IDs 10743–10778 = India + 35 states/UTs), MDDS-coded
D-02 XLSX per state, pulled by `data/raw/census_migration/fetch_census_d02.py` (official
export, keyless; the XLSX are git-ignored ~50 MB, the fetcher + `_manifest.csv` are
committed; note the portal serves an incomplete TLS chain — the fetcher documents the
verify workaround). Parsing quirks handled: single-district UTs label their only row
"Union Territory - X" with no district rows (the UT total IS the district); district rows
carry bare names (no "District -" prefix) and are selected by MDDS code ≠ 000.

All **640/640** census-2011 districts resolved, 0 unmatched; counts crosswalked extensively
onto the 785 frame (780 with values; the 5 unresolved 2021 Assam/Tripura reorg districts go
to the ladder). Reconciliation is a **triple tie**: panel == sum of state files == the
all-India file's national row, for both variables (recent 64,672,520; total 177,894,704).
Face check: largest recent in-migrant stocks are Bengaluru Urban (2.05M), Thane, Pune,
Surat, Mumbai Suburban — exactly India's documented migration magnets. The per-1,000 rate
is computed in the model layer on `pop_ghs_2025` (2011 stock ÷ current pop — conservative
dilution for fast growers, documented in the registry rationale).

## 5j · NCRP cancer incidence (via `scripts/harmonise_ncrp_cancer.py`) — v1.4.13

`ncrp_cancer_by_district.csv` (785 rows): **cancer_incidence_per100k** — the direct
oncology burden signal (need/chronic_burden). India has **no official district-level cancer
incidence** (the ~30 NCRP PBCRs are city/select-district; UP, Bihar etc. have none), so this
is a deliberate **state anchor**: ICMR-NCDIR National Cancer Registry Programme state/UT-wise
**estimated** incidence (data.gov.in resource `4b72b629`, both sexes / all sites, 2018–2022;
2022 national total **1,461,427** matches the published NCRP figure), converted to a crude
**rate** = state cases (2022) ÷ state `pop_ghs_2025` × 1e5 and assigned **flat within state**.
Dividing by population (not the size-dominated case COUNT) recovers the true per-capita
gradient: Kerala **164** and Mizoram **139** highest (documented highest-incidence
geographies), young Arunachal/Manipur/Sikkim and Daman/DNH (**40**) lowest. Within-state
oncology texture is left to the district-level `tobacco_use_pct` gradient (registry note).

Name reconciliation: NCRP spellings → LGD frame (Chattisgarh→Chhattisgarh, Pondicherry→
Puducherry, Jammu and Kashmir→…And…, A&N caps); **Daman + Dadra & Nagar Haveli summed into
the single merged UT**. All **36/36** frame states carry an NCRP figure (asserted up front),
so **785/785 districts are rated with no imputation**. Reconciliation: sum of per-state
assigned cases == source 2022 total exactly (1,461,427). Source folder
`data/raw/ncrp_cancer/` holds the committed `fetch_ncrp_cancer.py` (data.gov.in API, key from
`DATAGOVIN_KEY` in `.env`) + `_manifest.csv` + the small committed CSV.

## 5k · NFHS-5 chronic treatment-realization rate (derived, in `harmonise_nfhs5.py`) — v1.4.13

`nfhs5_by_district.csv` gains **chronic_treat_realiz_pct** (address/care_utilization) — the
chronic analogue of the acute `child_acute_careseek_pct`: how well NCD need converts into
sustained, effective treatment. The factsheet has **no standalone treated-share indicator**,
so it is **derived** (`cascade_source()`) from the blood-sugar / blood-pressure blocks, each
of which reports measured-high prevalence *and* a composite "measured-high **OR taking medicine
to control it**": `[(#88 − #86 − #87) + (#94 − #92 − #93)] / (#88 + #94) × 100` = the share of
the diabetes+hypertension composite pool that is **controlled on medication** (the medicated
group reads normal, so it enters the composite only via "taking medicine"). Because it is a
household-survey biomarker + self-report measure it is **independent of the HMIS OPD-visit
stream** — max ρ ≈ 0.49 vs any scored chronic feature, which is why it clears the redundancy
gate where the earlier held-data OPD-ratio version failed at ρ 0.94–0.96 (see model README
§v1.4.13). It rides the same crosswalk + parent-inherit + coverage path as every NFHS column
(a rate, `extensive=False`, never apportioned); **783/785 observed** (2 districts miss a
component). The six source indicators (#86,87,88,92,93,94) are used only to build the ratio —
they are **not** scored themselves. A synthetic row documents it in
`nfhs5_variable_dictionary.csv`.

## 5l · NFHS-5 central-obesity lifestyle risk (SEL #80, in `harmonise_nfhs5.py`) — v1.4.14

`nfhs5_by_district.csv` gains **central_obesity_pct** (need/chronic_burden) — a plain SEL
addition of NFHS-5 indicator **#80, women with high-risk waist-to-hip ratio (>0.85)**, i.e.
**central/abdominal adiposity**, new to NFHS-5 (no NFHS-4 trend column). It is the WHO-endorsed
"thin-fat phenotype" metabolic-risk marker for South Asians — predicting T2D/CVD demand better
than BMI and capturing the normal-BMI-but-abdominally-obese pool `obese_women_pct` (#79) misses.
Redundancy gate is the cleanest in the registry: max |ρ| **0.17** (anaemia, −), just **+0.09 vs
BMI-obesity** — an orthogonal dimension of chronic latent need, not a re-count. Rides the same
crosswalk + parent-inherit + coverage path (a rate, `extensive=False`); **705/785 direct →
785/785 observed after secondary/inherit**. Scored at w 2/3/0 (risk-factor tier == tobacco). The
SEL now carries **37 variables** (was 36). Full movers/diagnostics in model README §v1.4.14.

## 6 · Reproduce
```bash
python scripts/build_crosswalk.py               # our 3 rows + the consolidated table
python scripts/harmonise_team_sources.py        # teammate pulls: HMIS, Jan Aushadhi, SECC
python scripts/apportion_public_health_infra.py # ESTIMATE only, see §5c -- not real district data
python scripts/harmonise_osm_pois.py            # OSM POIs (needs data/raw/osm_pois/, see §5d)
python scripts/harmonise_pmjay.py               # PM-JAY hospitals (needs data/raw/pmjay_hospitals/, see §5e)
python scripts/harmonise_current_pop.py         # GHS-POP population (needs data/raw/ghs_pop/, see §5f)
python scripts/harmonise_vahan.py               # VAHAN registrations (needs data/raw/vahan/, see §5g)
python scripts/harmonise_hmis_seasonality.py    # HMIS monthly seasonality (see §5h)
python scripts/harmonise_census_migration.py    # Census D-02 migration (needs data/raw/census_migration/, see §5i)
python scripts/harmonise_ncrp_cancer.py         # NCRP cancer incidence (needs ghs_pop crosswalk + data/raw/ncrp_cancer/, see §5j)
```
Needs `data/raw/{data_gov_in,medical_education_tertiary,economic_financial_access,district_master_lgd,
data_hmis,janaushadhi_medicine_access,data_secc}/` and the backbone
`data/processed/district_frame/{view2,view3}` present (run `notebook/build_views.py` first if missing).
Python deps beyond pandas/numpy: `shapely` (§5d/§5f point-in-polygon), `rasterio` (§5f zonal
sums), `xlrd` (§5e PM-JAY .xls).
