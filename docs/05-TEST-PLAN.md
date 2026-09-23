# 05 · Test Plan

**Owner:** Validation owner
**Purpose:** evidence from the ten end-to-end test scenarios, with outcomes.
**Weight:** 15% of the grade, plus the credibility of everything else in the report

---

## 1. What this plan covers

> "The system should run end-to-end with at least 5–10 test cases showing **different outcomes**."
> "Document what worked, what **didn't**, and any **surprising** results."

Two things follow. The outcomes must genuinely differ — ten variations of "fraud detected" is one
test, not ten. And the write-up must include failures. **Every other team will demo happy paths.**
Showing where your own system breaks is the cheapest differentiator available and it makes the Q&A
unassailable, because you will already have found the hole the panel was going to poke.

---

## 2. The ten scenarios

| # | Input | Expected action | Proves |
|---|---|---|---|
| **S1** | Admission recorded 13 days after the beneficiary's death — cardiology hospital in **Ahmedabad** (101 providers) | `SUSPEND` | The pipeline decides and acts unattended |
| **S2** | **Identical pattern** — the **sole** cardiology provider in **Bahraich** (4.16 M people, 83 km to Gonda, aspirational) | `ESCALATE_SEC` | ★ **The thesis: same fraud, different decision** |
| **S3** | Zero length of stay on a major surgical package (laparoscopic hiatus hernia repair, every mandatory document on file); the surgery was performed and the patient left against medical advice that evening (LAMA form) | `RELEASE_CLAIM` | False positives are released, not left hanging |
| **S4** | Cardiology package billed by a hospital the **real** registry does not list for cardiology | `SHOW_CAUSE` | A detection running on real published data |
| **S5** | Discharge summary reused across two beneficiaries at a **PHC** listed as its district's sole radiation-oncology provider; site visit and beneficiary call on file | `DELIST_SPECIALTY` | Phantom capability — the gate inverts |
| **S6** | Same surgeon billed in two districts 250–600 km apart on one day; hospital-visit and beneficiary-call reports on file | `SUSPEND` | ★ **Branching:** channels follow the evidence |
| **S7** | Third acute admission in 30 days: three clinically distinct episodes, none with the investigation reports or case papers its package requires, so the desk can neither clear nor convict | `FIELD_AUDIT` → beneficiary call | ★ **Branching:** buys evidence instead of guessing |
| **S8** | Same pattern; field reports conflict (0.60 for, 0.55 against) | `NO_ACTION` (human) | Low confidence is defined, not accidental |
| **S9** | S2's case with the model API unreachable (Claude or Gemini) | `ESCALATE_SEC`, `degraded=true` | Designed degradation, not a crash |
| **S10** | No discharge date; district name "Bilaspur" matches two LGD codes | `REFUSE` | Never fabricates to keep the pipeline moving |

**Every scenario is selected from real data and asserted** in `generate/fixtures.py` — if the data stops
supporting a scenario, the build fails rather than testing a fiction. A separate test asserts each
scenario fires **only** its intended trigger. Run them: `python -m crew.run --scenarios`.

### 2.1 T1 and T2 — construction

These two must differ in **exactly one variable**. Same trigger, same severity, same evidence,
same claim amount, same package code. Only `district_code` changes.

```python
# tests/test_scenarios.py::test_thesis_same_fraud_different_decision
# S1 and S2 share trigger, package, amount, length of stay and findings;
# only the hospital's district network differs.

t1 = base.model_copy(update={"district_code": AHMEDABAD})   # 101 cardiology providers
t2 = base.model_copy(update={"district_code": BAHRAICH})    # 1 cardiology provider

assert process(t1).action == Action.SUSPEND
assert process(t2).action == Action.ESCALATE_SEC
```

**If those two assertions pass, the project's central claim is demonstrated in code.** Write them
first; everything else supports them.

### 2.2 T6 and T7 — the branching proof

Assert on `channels_used`, not just on the action:

```python
assert Channel.HOSPITAL_VISIT in process(t6).channels_used
assert Channel.DESK_AUDIT     in process(t7).channels_used
assert process(t6).channels_used != process(t1).channels_used
```

An examiner asking "why CrewAI and not a script?" gets an executable answer.

---

## 3. Acceptance criteria

| ID | Criterion | Threshold |
|---|---|---|
| AC-1 | All ten scenarios produce the expected action | 10/10 |
| AC-2 | Distinct terminal actions across the ten | ≥ 6 |
| AC-3 | T1 and T2 differ **only** in `district_code` | Asserted in code |
| AC-4 | T6 and T7 use different channels than T1 | Asserted in code |
| AC-5 | T9 completes with `degraded = true`, no exception escapes | Pass |
| AC-6 | T10 produces `REFUSE` with a populated reason code | Pass |
| AC-7 | Two runs with the same seed produce identical logs | Byte-identical |
| AC-8 | No real hospital name appears in any output artefact | grep returns nothing |

AC-8 is not optional. Add it as a CI-style check:

```python
def test_no_real_identity_leaks_into_artefacts():
    real = set(registry.hospital_name.str.upper())
    for path in Path("out/artefacts").rglob("*.md"):
        text = path.read_text().upper()
        assert not any(n in text for n in real), f"EC-1 violation in {path}"
```

---

## 4. Metrics to report

Three numbers beyond pass/fail. **These are what differentiate the report.**

> **Measured.** `python -m metrics.run` computes all three on a simulated year of flags and writes
> [09-EVALUATION](09-EVALUATION.md). Quote the numbers from there, not from this plan.

### 4.1 Auto-resolution share

> Of all flagged claims processed, what fraction reached a terminal action **without**
> `human_required`?

This is the number that proves Path A. Report it plainly. If it is low, say so and explain why —
a team that reports an uncomfortable number is more credible than one that reports none.

### 4.2 Threshold sweep

Sweep `DISTANCE_MATERIAL_KM` from 0 to 200 and plot two series: escalation volume, and the number of
districts left below minimum adequacy. Mark the chosen operating point.

**This converts the central claim from an opinion into a measurement.** Everyone else will write
"we escalate below X"; only you will show why X.

### 4.3 Disparate-impact audit

Auto-resolution rate broken down by `hospital_type`:

| Hospital type | Network share | Sole-provider share | Auto-resolution rate |
|---|---|---|---|
| Private (Not For Profit) | 4.1% | 7.1% | *measure* |
| GOI | 0.6% | 1.6% | *measure* |
| Public | 54.6% | 65.3% | *measure* |
| Private (For Profit) | 40.7% | 26.1% | *measure* |

Given that not-for-profits are 1.7× over-represented among sole providers, the rates will probably
differ. **Report it either way.** An honest adverse finding is worth more than a clean one, and it
directly evidences the legal-and-ethical-risk factor in the argument.

---

## 5. Failure log

Maintain `docs/TEST_RESULTS.md` as you go, not at the end. Structure each entry:

```
### F-nn · <what broke>
**Observed:**  what actually happened
**Expected:**  what should have happened
**Cause:**     root cause, verified not guessed
**Fix:**       what changed, or why it was accepted as a limitation
**Test:**      the assertion that now covers it
```

Three findings already belong in there before you write a line of code — see
[04-DATA-DICTIONARY §10](04-DATA-DICTIONARY.md#10-known-data-defects):

- **F-01** Non-participating states read as catastrophic network gaps
- **F-02** Post-2019 districts appear artificially thin
- **F-03** Punjab and Gujarat bulk-empanelment makes the capability flag a false-positive generator

These are the "what surprised us" section, and they are the strongest credibility signal in the
report. A team that finds its own traps is trusted on everything else.

### Found by the evaluation corpus (docs/09-EVALUATION.md §6)

All ten scenarios passed while both of these defects were present. The fixtures prove each path works;
only volume showed how often the wrong path was taken.

### F-04 · A death-register entry alone suspended hospitals
**Observed:**  in a simulated year of flags, 95.4% of innocent T10 flags (death-register errors)
               ended in suspension — about 19,700 wrongful suspensions a year at national scale, claim-level
**Expected:**  a register error is caught before an irreversible action
**Cause:**     `desk_audit` scored any register date before admission as fraud at 0.95, with or without a
               death certificate; the guidebook verifies that mismatch in the field
**Fix:**       with a certificate on file the desk still decides; without one, the case goes to the field
               (LLD B-10). Wrongful suspensions fell to about 2,400 a year
**Test:**      `test_a_death_register_entry_alone_orders_field_verification`; S1, S2, S9 unchanged

### F-05 · One field report could suspend a hospital
**Observed:**  after F-04, 10.0% of innocent T10 flags were still suspended — a single wrong
               field report above the 0.70 floor was enough
**Expected:**  no single piece of field evidence decides an irreversible action
**Cause:**     egregious triggers T6 and T10 ordered one field channel each
**Fix:**       every egregious trigger orders two independent channels; disagreement falls below the floor
               and goes to a human (LLD B-11). Wrongful suspensions fell to about 1,100 a year; frauds enforced
               moved from 82.4% to 82.4%
**Test:**      `test_no_single_field_report_can_suspend_a_hospital`; re-measured in `results/ablation.csv`

### Found by the first live run of the crew (Gemini, 16 September 2026)

The deterministic suite passed 10/10. The same scenarios run by a live model passed 8/10 on the first
attempt, and all three findings below were ours, not the model's.

### F-06 · The rules released a claim for surgery that was never performed
**Observed:**  on S3 the Gemini desk agent returned *inconclusive*: the LAMA form explained the zero-day stay,
               but the discharge summary said "Procedure deferred" while a full major-surgery package was billed
**Expected:**  the fixture meant to show a genuine false positive being released
**Cause:**     the fixture contradicted itself, and the rules path released on the LAMA form alone — a
               same-day discharge explains a zero stay, not billing for a procedure the file says never happened
**Fix:**       S3 now describes a genuine false positive (surgery performed, patient left that evening); the
               rules desk no longer clears a same-day discharge when a document says the procedure was deferred,
               postponed, cancelled or not performed — it orders the field channels instead
**Test:**      `test_a_lama_form_does_not_explain_billing_a_deferred_procedure`
**Re-run 2:**  still inconclusive, and rightly: the file lacked the package's mandatory documents (clinical
               notes and imaging before; operative note and X-ray after). NHA's trigger-2 checklist says "Verify
               mandatory documents for blocked procedure"; the rules path checks only the LAMA/DAMA/DOR item.
               S3 now carries the mandatory documents
**Re-run 3:**  still inconclusive: with every document present, the agent read the checklist's patient questions
               ("Was any surgery done or not?") as prerequisites. The desk task now says field-channel items are
               what the field answers when documents cannot settle a case, not prerequisites for one they do.
               S3 then released at 0.95
**Rules fix:** the rules desk now checks the mandatory-documents item as well. It may clear a claim
               only when the package's required categories are on file (discharge summary; pre-procedure
               evidence; an operative or procedure note when the package asks for one). The HBP master states
               requirements in prose, so the rules check categories and the crew reads the prose. The
               evaluation corpus now carries each package's mandatory uploads, as the claim system requires
**Test:**      `test_the_desk_cannot_clear_a_claim_missing_mandatory_documents`
**Why it matters:** the crew applied the guidebook checklist more completely than the rules did (risk R-02),
               and three runs showed how much a desk agent's caution depends on how its role is stated

### F-07 · The live crew would not treat a reused document as proof without a site visit
**Observed:**  on S5 the agent saw the identical discharge summary but asked for the hospital register and the
               beneficiary before concluding; the policy ordered a field audit instead of the de-listing referral
**Expected:**  `DELIST_SPECIALTY`
**Cause:**     a legitimate difference in caution, and the same concern as limitation 1 in 09-EVALUATION
               (a clerical mix-up reads as document reuse). The rules path treats reuse as desk-level proof
**Fix:**       S5 now carries the site visit and beneficiary call a de-listing referral should rest on, so it
               reaches the gate on either path. Whether T6 should *suspend* a well-served hospital before any
               verification was left to the team, which decided on 17 September that it should not: a reused
               document now withholds the claim and waits for both field channels (LLD B-28)
**Test:**      `test_phantom_referral_rests_on_site_verification`;
               `test_a_reused_document_at_a_well_served_hospital_waits_for_the_field`

### F-08 · A drafted notice overstated the access loss
**Observed:**  the S2 escalation said Bahraich would lose its "only healthcare facility". It would lose its only
               cardiology provider; the district has 87 hospitals
**Expected:**  the access consequence stated for the specialty at stake
**Cause:**     the drafting agent received reason codes (`PROVIDERS_IN_DISTRICT=1`) but not the specialty's name
**Fix:**       the drafter now gets the access facts in words, specialty named, with an instruction never to imply
               the district loses all care
**Re-run:**    the S5 notice then said the hospital "has been delisted" — the decision was a referral. The drafter
               now also gets each action in words, including what it does not do ("Nothing has been de-listed;
               the Committee decides"); the next S5 notice said exactly that
**Hardened:**  instructions reduce overstatement; they cannot guarantee it. Every drafted explanation now
               passes a deterministic check before it ships — no "only hospital/healthcare facility", no
               "without healthcare", no claim that a hospital is suspended or a specialty de-listed unless that
               is the action (negations such as "nothing has been de-listed" pass), no other hospital
               identifier. A failing draft is replaced by the template explanation and logged. Both
               overstated drafts from the first live run fail the check; the accurate final ones pass
**Test:**      `test_the_officer_reads_the_decision_and_the_access_at_stake_before_drafting`,
               `test_an_overstated_draft_is_sent_back_and_revised`,
               `test_an_officer_that_never_writes_a_publishable_draft_leaves_the_template`, `test_draft_checks`
**Since F-54:** the drafter is the Enforcement Officer. It reads the access facts through `access_impact`, and a
               failing draft is refused by its action tool and goes back to it with the reasons (at most three
               times) rather than being silently replaced

### F-09 · A registry agent turned "consistent" into "clears the claim"
**Observed:**  on the fourth live run S5 went to human review at confidence 0.697: desk (0.90) and both field
               reports (0.95, 0.90) supported fraud, but the registry agent answered *opposes* at 1.00 because
               the hospital is empanelled for the specialty
**Expected:**  `DELIST_SPECIALTY`, as on the two runs before
**Cause:**     a guardrail gap. A registry can say "not empanelled" (supports) or "consistent" (neutral); it can
               never clear a claim. The override only caught agents that wrongly *supported* fraud
**Fix:**       the registry lookup's stance now wins in both directions; the agent's reading survives only
               when its stance matches
**Test:**      `test_the_registry_finding_is_the_lookup_on_the_crew_path` (since F-54 there is no registry agent:
               the registry finding is the lookup's on both paths)
**Extended:**  the same hole existed for field reports: the agent could flip a field team's recorded finding,
               inflate its confidence, or silently drop it. Now the recorded stance always stands; the agent
               may only discount a report (confidence is capped at the field team's), and a report it omits
               or contradicts is kept as recorded, with a note
**Test:**      `test_an_agent_cannot_flip_drop_or_inflate_field_evidence`
**Why it matters:** run-to-run variation in a live model found a hole that two passing runs had hidden

### F-10 · A package name could justify a padded stay
**Observed:**  found while fixing F-06: the T4 desk rule searched every document for "sepsis", "complication",
               "critical" and similar. A discharge summary that merely repeats a package name ("Severe sepsis",
               "HIV with complications") therefore cleared an unjustified 10+ day stay
**Expected:**  justification comes from clinical documentation
**Cause:**     the rule's search was wider than its own stated logic ("clinical notes document a complication")
**Fix:**       the T4 desk rule reads clinical documents only (clinical notes, progress notes, ICU notes, case sheet)
**Test:**      `test_a_long_stay_is_justified_by_clinical_notes_not_a_package_name`
**Measured:**  3 of 548 T4 cases in the evaluation corpus moved from release to show-cause; frauds enforced
               82.7% → 82.8%. Every other headline figure is unchanged

### Found by the code audit (17 September 2026)

A line-by-line review of every module, static analysis (ruff: pyflakes, bugbear, pylint errors), a rebuild of
all reference data from source (all 15 files byte-identical), and both model SDKs exercised for real — the
Anthropic SDK through a mocked HTTP transport, Gemini live. Every row has a test that fails without its fix.

| # | Observed | Cause | Fix | Test |
|---|---|---|---|---|
| F-11 | **The pipeline crashed** on an egregious case in any of 23 districts without boundary geometry (167 listed hospitals) | adequacy lookup required geometry; the escalation text then read fields of `None` | adequacy uses `district_network.csv` when geometry is missing (distance unknown); the explanation handles unknown access | `test_districts_without_geometry_still_get_adequacy` |
| F-12 | R1 fired on **every claim from 9,269 hospitals (30%)** whose registry lists no specialty | a blank list was read as "not empanelled", contradicting data defect D-4 | R1 needs a non-empty list; an egregious case at such a hospital escalates with access marked unknown | `test_a_blank_registry_list_is_not_evidence_and_escalates_unknown_access` |
| F-13 | A registry mismatch counted as evidence for **any** trigger: T7 with an inconclusive desk at an unlisted hospital got a notice | registry verification supported fraud whatever the primary trigger | the registry is evidence for R1 only | `test_registry_evidence_counts_only_for_r1` |
| F-14 | **One field report could still suspend a hospital** when only one of the two ordered channels had reported | the policy received "any reports on file", not which channels | `decide()` receives the channels on file; an egregious finding resting on field evidence waits for every ordered channel; weak partial evidence orders the missing ones | `test_one_field_report_cannot_suspend_while_another_channel_is_outstanding` |
| F-15 | A hospital not empanelled for the billed specialty was protected as its district's "sole real provider" | the gate ignored whether the flagged hospital was itself a provider | not empanelled for it → no empanelled access to protect | `test_a_hospital_not_empanelled_for_the_specialty_is_not_protected` |
| F-16 | The crew's desk agent could clear a claim missing mandatory documents, or convict on a death-register entry alone | the evidentiary rules lived only in the rules desk | `desk_preconditions()` binds both desks | `test_the_crew_desk_cannot_clear_a_claim_missing_mandatory_documents`, `..._convict_on_a_register_entry_alone` |
| F-17 | A discharge before admission read as a **zero-day stay** and fired T2/T3 | `los_days` clamps negatives to 0 and intake did not check the timeline | intake refuses discharge-before-admission and submission-before-discharge | `test_malformed_timelines_are_refused_not_read_as_zero_stays` |
| F-18 | Any two claims with a blank document fired **document reuse** | every blank text shares one SHA-256 | blank documents are not indexed or compared | `test_blank_documents_are_not_document_reuse` |
| F-19 | R3 fired on the 71 hospitals with **no recorded type** | "not public" was read as "private" | R3 needs a known private type | `test_r3_needs_a_hospital_known_to_be_private` |
| F-20 | `scripts/build_lookups.py` **crashed** (README quickstart step 2); `triggers.json` recorded R1 at severity 3 and a PDF page footer in T10's checklist | the guard still read the v1 column `spec`; the file predated LLD B-5 | guard rewritten for canonical specialties (asserts the 292 Paediatric Cancer cells); R1 severity 2; footers stripped | rebuild verified; outputs diffed |
| F-21 | On a live run after the audit, **S2 — the thesis case — went to a field audit**: the agent confirmed death 13 days before admission from the certificate, then asked to verify with the family "as outlined in the guidebook checklist". A notice was also rejected because the model echoed our own instruction in the negative ("will not lose all healthcare … or its only hospital") | the case file showed NHA's checklist as one flat list; the PDF table flattens so that desk items and field questions are indistinguishable. The draft check judged negation within 30 characters only | the guidebook's desk-audit column is transcribed per trigger (`DESK_CHECKS`) and shown apart from field checks, which are labelled as not prerequisites; field-audit orders list field checks only; negation is judged per sentence; the drafting instruction no longer quotes the phrases it forbids. S2, S3 and S7 then passed on two consecutive live runs | `test_draft_checks` (negated and un-negated cases) |

### Found by the second code audit (17 September 2026)

A second line-by-line review, this time reading every rule against the data it runs on and every sentence the
system writes against the facts it states. Every row has a test that fails without its fix.

| # | Observed | Cause | Fix | Test |
|---|---|---|---|---|
| F-22 | **The access gate checked only the billed specialty**, but suspension takes a hospital out of the scheme for *every* specialty it is empanelled for. Under the evaluation's allocation model, **6.5% of confirmed egregious findings** landed on a hospital that was clear for the billed specialty yet its district's only real provider of another one. Those were suspended automatically, a larger share than the 4.3% the gate escalated. BO-2 ("zero auto-suspensions of sole real providers") was not met | the gate was written for one specialty and applied to the billed one | `decide()` takes the adequacy of every empanelled specialty and has no default for it, so a caller cannot forget it. The hospital is escalated if any of its specialties would lose its only real provider. The explanation, the SEC pack, the drafting facts and the decision log (`specialties_at_stake`) name each one, and the evaluation's exact gate works per hospital | `test_suspension_is_gated_on_every_specialty_the_hospital_provides`, `test_suspension_checks_every_specialty_the_hospital_provides`, `test_the_exact_gate_is_the_policy_gate` |
| F-23 | **The desk read absences as evidence.** Progress notes saying "stable, no complications; non-critical course" justified a padded T4 stay and released the claim. "Not a LAMA case" explained a zero-day stay, and "surgery was not deferred" blocked a documented release | keyword patterns with no reading of negation | a small NegEx-style reading. A negation within five words before a mention, in the same clause, cancels it; so does an absence stated right after it ("Complications: nil", "ICU not required"). A bracketed abbreviation inherits the negation of the phrase before it | `test_a_long_stay_is_justified_by_clinical_notes_not_a_package_name`, `test_a_negated_discharge_explanation_explains_nothing` |
| F-24 | **A patient admitted and dying on the same day read as billed after death** whenever the death register holds a date. With the certificate on file, the desk "proved" it, so a well-served hospital would have been suspended | T10 compared timestamps, and a date-only death sits at midnight, before any same-day admission | T10 compares calendar dates, as the guidebook's "cross match DOA and date of death" does | `test_a_death_on_the_day_of_admission_is_not_billing_after_death` |
| F-25 | A claim that named its district was **refused as UNKNOWN_DISTRICT** for any of the 346 hospitals in the 23 districts without boundary geometry, and ambiguity was checked against geometry districts only | name resolution read only the geometry table (F-11's cause, in another function) | names resolve against every district in the network | `test_a_district_without_geometry_can_be_named` |
| F-26 | A registry agent could **re-weigh a registry fact**: an R1 mismatch read at 0.30 fell below the floor and bought a field audit the rules path never orders | the guardrail overrode a contradicting stance, but kept the agent's confidence when the stance agreed | the lookup's confidence binds as well as its stance | `test_the_registry_finding_is_the_lookup_on_the_crew_path` (the registry agent was removed, F-54) |
| F-27 | `python -m crew.run --out <dir>` **deleted the directory before checking its arguments**: `--out results` would have removed the evaluation results, and `--out .` the project | `shutil.rmtree(out)` ran first, on any path | arguments are checked first, and only this program's own outputs are removed; a directory holding anything else is refused. The run exits non-zero if a scenario fails, and the log refuses to append under another version's header | `test_the_runner_never_deletes_a_directory_it_did_not_write`, `test_a_log_from_another_version_is_never_appended_to` |
| F-28 | The draft check **passed a de-listing stated on the line after a negated one**. It also passed "will be suspended" for an escalation, and "de-empanelled" | a line break did not end a sentence, and the future tense and NHA's own word were missing from the patterns | line breaks end a sentence; "will be", "shall be", "is being" and "de-empanelled" are checked | `test_draft_checks` (4 new cases) |
| F-29 | A model's `NaN` confidence **became 1.0** | `max(0, min(1, nan))` returns 1.0 | a non-finite confidence counts as 0 | `test_a_nan_confidence_is_no_confidence` |
| F-30 | T7 counted admissions **up to 60 days apart** as "within 30 days", including admissions made after the claim | the window ran 30 days either side | the window is the 30 days up to and including this admission | `test_repeat_admissions_are_counted_in_the_30_days_to_this_one` |
| F-31 | The corpus build **could crash** on a T5 claim whose specialty had no provider far enough away | the second district was drawn from a list that could be empty | a T5 claim is placed only where a far provider exists; the corpus is unchanged at the evaluated seeds | `test_a_t5_claim_is_placed_only_where_a_far_provider_exists` |
| F-32 | Explanations **misstated the access facts** in five places: "the only real provider" for one of two providers in an aspirational district; "has 1 empanelled providers … so suspension does not remove access" for a sole provider with a neighbour 31 km away; "no alternative listed" for a district with 101 providers; "empanelled for None"; and "the documents cannot settle" when weak field evidence was on file. On the live run after this audit, the drafting facts gave "101 providers" and "100 other providers" side by side, and the model wrote "101 other providers" | template wording written for one case of each; the facts stated one count two ways | each sentence is built from the facts it reports; the drafting facts state one count and what it includes (the re-run draft said "one hundred other empanelled providers") | `test_explanations_state_the_access_facts_exactly` |
| F-33 | **Trigger 2 could never fire on 17 major burns surgeries** (Rs 30,000–80,000, skin grafting and flaps), and trigger 3 never on acute exacerbation of COPD or respiratory failure, because all were on the day-care list. Neither package table had a build script | the day-care name rule matched substrings anywhere in the text: "follow-up dressings" inside a grafting package, "opd" inside COPD, "following conditions" in neonatal intensive care | `scripts/build_packages.py` rebuilds the rates table byte for byte, and the day-care list with a word-bounded rule applied to the package's own name. 24 inpatient packages leave the list (119 → 95) and nothing else changes | `test_inpatient_packages_are_not_day_care`; rebuild diffed against the old files |
| F-34 | The LLD's error table described behaviour the code does not have: a `RARE_SPECIALTY` skip, and "retry once" on invalid agent output | documentation drift | the table now states what happens: CrewAI retries a failing agent task up to twice (`max_retry_limit`), then the case degrades | doc corrected |

**Measured after the second audit** (`python -m metrics.run`, same corpus and seed): of confirmed egregious findings, **10.82% are escalated, up from 4.34%**; the difference is exactly the suspensions that removed another specialty's only real provider (F-22). Innocent flags suspended fell from 0.65% to 0.60%, and wrongful suspensions from 1,059 to 974 a year. Frauds enforced rose from 82.4% to 82.7%; auto-resolution moved from 87.1% to 87.0%.

**Live on Gemini after the audit:** 10/10. The primary model was rate-limited (429) and the backup took over at once; every drafted explanation passed `draft_problems()` and shipped. S2's escalation named cardiology and Gonda at 83 km, and S5's referral listed what a suspension would also remove.

### Found by the third audit: edge cases (17 September 2026)

Every input was attacked on purpose: claims built with offsets, blanks, misspelled fields, hostile ids and forged hashes;
packages as the published master actually lists them; model output that is empty, infinite or wrong about recorded
facts; and every command run carelessly. A probe script confirmed each suspect before it was fixed, and every row has a
test that fails without its fix (`tests/test_edges.py` unless named).

| # | Observed | Cause | Fix | Test |
|---|---|---|---|---|
| F-35 | **R1 could accuse half the network for legitimate billing.** 473 of 1,602 packages are listed under two to four specialties, but the code kept only the listing whose file sorted first: `MP001C`, a paediatric package, read as General Medicine. **15,419 hospitals** were exposed to an R1 show-cause notice for a package they are empanelled for. 52 packages are government-reserved under one listing only, so a private paediatric hospital billing `MG007A` drew R1 **and** R3 | the rates table has one row per package, and the loader took that row's specialty and reserved flag as the package's | `build_packages.py` also writes every listing (`hbp_package_listings.csv`). A package's own specialty is the one its code names. R1 fires only when the hospital is empanelled for none of the listings. R3 fires only when the package is reserved under every listing the hospital could bill it through. Adequacy and trigger scope use the specialty the hospital bills through | `test_a_package_listed_under_two_specialties_is_billable_through_either`, `test_a_package_reserved_under_one_listing_is_open_through_another`, `test_adequacy_is_for_the_specialty_the_hospital_bills_through` |
| F-36 | **Two different documents could be "byte-identical"**: a hash supplied with a document was trusted, so a forged or stale hash fired T6, an egregious trigger | `Document` computed the hash only when none was given | a supplied hash must match the text, and the claim store recomputes every hash from the text | `test_a_supplied_document_hash_must_match_its_text`, `test_two_different_documents_cannot_share_a_stale_hash` |
| F-37 | **A claim id wrote a file outside the output directory**: `..\..\..\..\pwned` produced `pwned.md` in the temp folder's root | the claim id became part of the artefact's file name unchecked, and `model_copy()` builds a claim without running validators | claim ids are 1-64 plain characters; `process()` re-validates every claim; the artefact writer refuses any path outside `artefacts/` | `test_claim_ids_are_plain_tokens`, `test_an_unvalidated_claim_id_never_reaches_the_file_system` |
| F-38 | **Malformed claims were read, not refused.** A misspelled field (`beneficiary_death_date`) was silently dropped, so T10 could never fire. A timestamp with an offset beside one without **crashed** intake (`TypeError`). `" reg-sim-5501 "` and `REG-SIM-5501` were two surgeons, and a blank beneficiary id joined every blank claim into one person | the claim schema ignored unknown fields and compared identifiers and datetimes as given | unknown fields are errors; timestamps with an offset are converted to Indian time; identifiers are trimmed and upper-cased, and a blank beneficiary is refused | `test_a_misspelled_claim_field_is_an_error_not_a_silent_omission`, `test_timestamps_with_an_offset_are_read_in_indian_time`, `test_identifiers_are_compared_as_the_same_person_or_surgeon` |
| F-39 | **A death certificate that contradicted the register still convicted.** With a certificate dating death two days *after* admission on file, the desk read "certified death" and the hospital was suspended | the desk checked that a certificate existed, not what it said | the certificate must itself date the death before admission (read in the common formats); one dating it on or after admission, or with no readable date, leaves the desk inconclusive and orders field verification | `test_a_certificate_dating_death_after_admission_contradicts_the_register`, `test_a_certificate_with_no_readable_date_proves_nothing`, `test_common_date_formats_on_a_certificate_are_read` |
| F-40 | **A district name contradicting the district code was ignored**: Ahmedabad's code with the name "Bahraich" was suspended as Ahmedabad | the name was read only when the code was missing | a name that resolves to a different district is refused (`DISTRICT_NAME_CODE_MISMATCH`) | `test_a_district_name_that_contradicts_the_code_is_refused` |
| F-41 | **Field evidence could be counterfeit in form.** A "field report" on the desk channel decided a show-cause notice at 0.95, and three copies of one beneficiary-call report counted as three independent pieces of evidence | reports were weighed without checking their channels | intake refuses a field report on the desk channel and a channel reported twice | `test_field_evidence_is_one_report_per_field_channel` |
| F-42 | **An empty draft shipped as an empty explanation.** Drafts saying the withheld claim "has been released and will be paid", runaway drafts, and drafts opening their own Markdown heading all passed. Agent findings, which go into artefacts verbatim, could name another hospital's identifier | the checks covered only access overstatement and the action taken | drafts must be 25-400 words with no heading and no claim that the claim is released or paid; other hospital identifiers in agent text are replaced | `test_draft_shape_checks`, `test_a_draft_saying_the_claim_is_not_released_passes`, `test_agent_text_cannot_name_another_hospital` (test_gemini_contract.py) |
| F-43 | **The documented trial command overwrote the published evaluation.** `python -m metrics.run --n 1000 --quick` replaced `results/` and `docs/09-EVALUATION.md` with a smaller run whose ablation and sensitivity sections read "Not run". `--n 0` crashed on a division by zero | every run wrote to the published paths, and nothing checked the arguments | only the full default run publishes; a trial writes to `results/trial-n<N>[-quick]/`. `--n` must be at least 50, and the corpus settings are range-checked | `test_a_trial_evaluation_never_touches_the_published_one`, `test_corpus_settings_out_of_range_are_rejected` |
| F-44 | **A model's desk reading could clear a recorded fact**: "no fraud" on a T10 claim whose certificate dates death before admission, or on a T6 claim with a document filed for another beneficiary, released the claim | the preconditions stopped the desk convicting on too little, not clearing against a fact | for T10 and T6 the desk cannot clear what the file records; only the field can explain it | `test_the_crew_desk_cannot_clear_a_recorded_fact` (test_gemini_contract.py) |
| F-45 | **A confidence just below the floor acted**: 0.6996 was rounded to 0.700 before the comparison | the aggregate was rounded, then compared | decisions compare the exact value; the recorded value is rounded *down* to two places, so it can never be printed as meeting a floor it is below | `test_the_floor_is_applied_to_the_unrounded_confidence` (test_policy.py) |
| F-46 | Smaller ways to break it: "Bed No 12 shifted to ICU" read "No" as a negation; "Sepsis? No" and "Sepsis: negative" did not; an infinite model confidence became 1.0; `GEMINI_MAX_RPM=ten` crashed with a bare `int()` error; `--out` would empty an `artefacts/` folder holding someone's notes; a refused log left an artefact with no log row; an empty log file was refused as "different columns" | edge cases of the earlier fixes | each handled, and each tested | `test_clinical_shorthand_is_read_correctly`, `test_a_nan_confidence_is_no_confidence`, `test_a_bad_gemini_setting_is_a_clear_error`, `test_the_artefacts_folder_is_not_emptied_of_files_the_run_did_not_write`, `test_a_refused_log_leaves_no_artefact_behind`, `test_an_empty_log_file_is_started_afresh` |

**Team decision (B-28), made during this audit: a reused document waits for the field.** A byte-identical document
filed for another beneficiary (T6) still withholds the claim, but suspension now needs a hospital visit and a
beneficiary call to confirm it. At the desk, reuse and an honest upload error look the same. The live crew had asked
for exactly that on S5 (F-07). Only a death certificate that dates death before admission (T10) remains proof at the
desk. Locked by `test_a_desk_finding_that_is_not_proof_in_itself_waits_for_the_field` and
`test_a_reused_document_at_a_well_served_hospital_waits_for_the_field`.

**Measured after the third audit** (same corpus seed): auto-resolution 87.0% → **86.4%**, and frauds enforced 82.7% → **80.1%**, because every reused document now goes to the field (T6: 69% suspended after two confirming reports, 27% to a human when they disagree). Innocent flags suspended moved from 0.60% to **0.68%** (974 → 1,101 a year). **That is sampling noise, not a regression:** every wrongful suspension, at every seed, is a T5 or T10 false flag where the desk was inconclusive and both field reports were wrong. The package-listing fix changed the corpus draws, and across four seeds the rate runs from 0.42% to 0.68%. The gate figures (10.82% escalated, 0.07% referred) are exact and did not move.

**Live on Gemini after the third audit:** 10/10, exit code 0. The primary model returned 503, and after one quick retry the backup served the run. Every draft passed the stricter checks. S4's notice names the package's listings.

### Found by the fourth audit: the data under the numbers (17 September 2026)

This round audited what the code rests on, not only the code. It checked where every hospital is placed, how the
package master's own columns are read, whether the provenance checksums verify, and whether the evaluation's
calibration pairs the right districts. Tests are in `tests/test_data.py` and `tests/test_edges.py` unless named.

| # | Observed | Cause | Fix | Test |
|---|---|---|---|---|
| F-47 | **The calibration evidence paired the wrong districts.** Faizabad (renamed Ayodhya) was fuzzy-matched to **Firozabad**, a different district, so Firozabad's data point held two districts' admissions. Allahabad (now Prayagraj) and Dohad (Dahod) matched nothing and were dropped. The published R² table (09-EVALUATION §2) was computed on those pairs, and its prose claimed "similar exponents in both states" | `difflib` closest spelling with a 0.75 cutoff, and no check that each district was used once | exact names, then an explicit table of renamed districts, then close spellings only onto unclaimed districts. Every published district must match exactly once, or the run fails. All 75 UP and 33 Gujarat districts now enter the fit (UP R² 0.573 → 0.526; Gujarat 0.887 → 0.862), and the prose is generated from the numbers | `test_calibration_matches_every_published_district_to_the_right_one`, `test_an_unknown_district_name_fails_rather_than_guessing` |
| F-48 | **Re-running quickstart step 1 broke the evaluation.** `build_reference.py` rewrote `figures.json` whole, dropping the "official" block that step 4 adds, so `metrics.run` failed with `KeyError: 'official'` until step 4 ran again | two scripts owned one file, and the first overwrote it | the reference build merges into the file with the same JSON settings as step 4; a rebuild of every reference table is byte-identical | rebuild diffed: 16 of 16 files identical |
| F-49 | **A stale crosswalk file**: `unmatched_pmjay.csv` still listed Ananthpur, Nellor, Raebareli and eleven more as unmatched, although the crosswalk had resolved them | the harmoniser wrote the file only when something was unmatched, so a clean run left the old list in place | the harmoniser always writes it; the file is regenerated from the current data and is header-only (the 208 legacy `-NA-` Hyderabad rows are dropped by design) | `test_the_unmatched_crosswalk_list_is_current` |
| F-50 | **No provenance checksum verified its file.** All 21 `csv_sha256` values in the OGD manifest failed against the CSVs on disk. An API page that came back empty ended the fetch loop and saved a short table silently | text-mode writes on Windows turned `\n` into `\r\n` after hashing; the loop broke on an empty page with no count check | CSVs are written byte-exact (the 21 files converted; every derived table byte-identical), and a fetch that returns fewer rows than the API reports writes nothing and fails. The registry manifest now records its SHA-256 too | `test_official_tables_match_their_manifest`, `test_the_registry_export_matches_its_manifest` |
| F-51 | **A referral letter cleared private billing of any government-reserved package.** The package master's `referral_basis` allows referral for only 9 reserved packages (caesarean and high-risk deliveries, two eye repairs); for the other 180 reserved listings a referral changes nothing, yet the desk released the claim. The corpus labelled such cases innocent | the column was never read | `Package.referral_allowed` from `referral_basis`. The rules desk supports R3 on a package with no referral route, and the crew desk cannot clear one. The corpus places innocent R3 flags only where a referral is possible | `test_a_referral_letter_clears_only_a_package_allowed_on_referral`, `test_the_corpus_places_innocent_r3_flags_where_a_referral_is_possible`, `test_the_crew_desk_cannot_clear_a_package_with_no_referral_route` (test_gemini_contract.py) |
| F-52 | **`--provider claude` without `--llm` ran the rules, silently.** A rehearsal meant for the final presentation would not have used Claude at all | the provider was read only inside the `--llm` branch | naming a provider without `--llm` is an error. A model run also ends by naming the models that actually served it, which matters when a rate-limited primary hands over to its backup | `test_naming_a_provider_without_llm_is_an_error` |
| F-53 | Re-fetching the registry overwrote its manifest's method, notes and row counts, and recorded no hash | the fetcher wrote a new manifest wholesale | it merges into the manifest, records each file's SHA-256, and warns that the pipeline reads the dated snapshot by name | reviewed; the manifest test covers the hash |

**Measured after the fourth audit** (same seed): auto-resolution **86.7%**, innocent flags suspended **0.63%** (0.44–0.63% across four seeds, every case two wrong field reports), frauds enforced **80.4%**, wrongful suspensions **1,016 a year**. The gate figures are unchanged (10.82% escalated, 0.07% referred). **Live on Gemini:** 10/10. The primary model returned 503, and the run now says which model served it (`gemini-3.5-flash-lite`).

**Checked and found sound this round:** every hospital is placed in a district of its own state (30,858 of 30,858).
Every fuzzy or alias crosswalk match is a genuine spelling variant or renamed district, and no two names share a
code. The basic-tier name heuristic flags 12,188 facilities, only 3 of them private. The inherited crosswalk scripts
are provenance only; one needs `rapidfuzz` and data not in this repository.

**Disclosed rather than fixed (R-18):** a pseudonym does not hide a hospital that network facts single out. S2's
hospital is the only cardiology provider in Bahraich, and the pseudonym mapping can be recomputed from the public
export.

**Checked and found correct:** crosswalk keys and hospital IDs are unique (no double counting); the reference data
rebuilds byte-identically; the Claude request (`fallbacks`, betas, structured output via `output_config`) is accepted
by the real SDK and keeps `null` answerable; 201 packages priced at 0 are per-day packages in the source JSON, not a
parse error.

---

### Found by rebuilding the crew (17-18 September 2026)

**The crew was decoration, and a review, not a test, found it.** The failure log above records what the crew caught
on its first live run (F-06 to F-09). It does not record what the crew was not doing, because no test asked:
- **No tools.** No agent had one: the orchestrator called every tool itself, and LLD B-3 deferred agent-callable tools
  "after live testing" and was never revisited.
- **Two agents with no effect.** The triage agent's plan was never read, and the registry agent was always overridden
  by the lookup.
- **Never evaluated.** The 5,000-case evaluation never ran the crew.

Every test passed. Risk R-02's mitigation asserted on `channels_used`, which tests the pipeline, not the agents.

| # | Observed | Cause | Fix | Test |
|---|---|---|---|---|
| F-54 | **The CrewAI crew did no work a function could not.** Of four agents only the desk agent changed a decision; none called a tool; the guidebook's cross-claim desk checks for T6 and T7 were impossible, because the desk saw one claim | B-3 deferred agent tools; audits added deterministic overrides until two agents were overridden entirely | Native tool calling in both adapters (thought signatures and thinking blocks kept across tool turns). **Two agents:** the Desk Investigator with five evidence tools, and the Enforcement Officer with the decision, the access impact and seven action tools, each of which executes only the policy's decision. The policy decides between them (task callback). A task guardrail requires cross-claim evidence before a stance on T5 to T7. Every artefact carries the investigation trail. A 40-case benchmark measures the agents against the rules (10-AGENT-EVALUATION). LLD B-32 to B-39 | `test_each_agent_has_only_its_own_tools`, `test_the_investigator_compares_related_claims_with_its_tools`, `test_a_stance_on_repeat_admissions_needs_the_other_claims_examined`, `test_the_investigator_cannot_read_a_claim_no_tool_has_shown_it`, `test_the_officer_cannot_execute_any_action_but_the_policys`, `test_an_overstated_draft_is_sent_back_and_revised`, `test_an_officer_that_never_acts_leaves_the_decision_to_the_orchestrator` (test_gemini_contract.py); `test_tool_use_goes_back_to_crewai_with_its_thinking_kept_for_the_next_turn` (test_llm_contract.py); tests/test_agent_tools.py, tests/test_benchmark.py |
| F-55 | **Live, the crew issued a show-cause notice on S3, the scenario that should release.** Its investigator read the package master: S3 billed a partial gastrectomy discharged the same day, and the file lacked the package's mandatory histopathology report and intra-operative photograph. The notice asked for exactly those | S3 took the median major surgical package, a gastrectomy, and filed only three of its document categories. The rules checked only those three, so they cleared it | S3 is now a laparoscopic hiatus hernia repair, a procedure a patient can plausibly walk out of the same evening, with every document its package names | `test_scenario_produces_expected_action[S3]`, live run 2 |
| F-56 | **Live, the crew released S7, the scenario that should order a field audit.** It compared the three admissions and found them clinically distinct, which they are. But none had the investigation reports or indoor case papers the package master makes mandatory, so the desk could not clear the claim | `missing_mandatory()` checked three of the eight document categories the package master names: discharge summary, pre-procedure evidence, operative notes | `required_documents(package)` reads all eight from the package's own requirement text (histopathology, photographs, post-procedure imaging, investigation reports and indoor case papers added), and both desks meet it. The corpus files the same categories, so the 1,000-case evaluation trial is **byte-identical** before and after. The investigator is also told that a missing document stops a clearance but is not, on its own, evidence of fraud | `test_the_mandatory_categories_are_read_from_the_package_master`, `test_a_claim_missing_its_histopathology_and_photograph_is_not_cleared`, `test_every_corpus_claim_files_every_category_its_package_requires` |
| F-57 | **A spent daily quota cost four minutes a call before failing.** Both free-tier models ran out on the benchmark (`gemini-3.8-flash` allows 20 requests a day, `gemini-3.5-flash-lite` 500), and each 429 carried a 53-second retryDelay, which the adapter honoured four times | the retry logic read only the delay, which is under a minute even for a quota that resets at midnight. The adapter also never went back to the primary once it had switched | a 429 naming a per-day quota (`QuotaFailure`, `...PerDay...`) marks the model spent and fails at once. When the backup runs out, the primary is tried again. A model left only for a short rate limit still gets its full retries | `test_a_spent_daily_quota_is_not_waited_on`, `test_when_the_backups_daily_quota_runs_out_the_primary_is_tried_again`, `test_with_one_model_spent_the_other_is_waited_on_for_a_short_rate_limit` |
| F-58 | **The crew cleared R2 with no invoice on file, and R3 with no referral** (benchmark BCH-R2-07, BCH-R3-06). It read each gap as an upload slip; on R3 it was following an instruction added for F-56 | the evidentiary preconditions required the package's mandatory documents, but not the document that would explain the trigger. The rules path never needed it, because it clears only on those documents | on both paths a clearance of R2 needs an invoice or bill on file, and of R3 a referral. The F-56 instruction now exempts the trigger's own explaining document | `test_the_crew_cannot_clear_a_trigger_whose_explaining_document_is_missing` |
| F-60 | **Two agents were one reading.** After the rebuild the crew had a Desk Investigator and an Enforcement Officer: one agent read the file and one carried out the decision, so a single reading, unchecked by anyone, was all the evidence the policy weighed. A desk audit, a clinical audit and the weight of a field report are three questions, asked of different evidence, and no one reviewed a finding before a hospital was acted on | The rebuild (F-54) removed the agents that did nothing and did not put substantive ones in their place; the team asked for the roster to be widened | **Six agents** (LLD B-42 to B-46): Medical Auditor (clinical need on T2, T3, T4, T7), Field Evidence Analyst (the weight of each field report) and Desk Investigator read independently — their tasks take no context, and each gets its own case file; the **Audit Reviewer** receives all three and may **dispute** one, which is then set aside (stance null, confidence 0), the case going on with the readings left standing; the **Committee Liaison** files the Committee's brief on a referral, checked like a notice. The policy still decides, now in the review task's callback | `test_the_medical_auditor_works_only_on_a_clinical_trigger`, `test_the_field_evidence_analyst_works_only_when_field_reports_are_on_file`, `test_the_readers_work_independently_and_the_reviewer_receives_all_three`, `test_a_disputed_reading_weighs_nothing`, `test_desk_and_medical_readings_that_disagree_buy_field_evidence`, `test_the_reviewer_may_dispute_only_a_reading_on_the_case_and_must_say_why`, `test_disputes_count_only_against_readings`, `test_a_medical_stance_on_repeat_admissions_needs_the_admissions_read`, `test_the_medical_auditor_is_bound_by_the_desks_preconditions`, `test_the_liaison_files_the_committee_brief_on_a_referral`, `test_an_overstated_brief_is_sent_back_and_revised`, `test_a_liaison_that_files_nothing_leaves_the_standard_question`, `test_the_committee_brief_is_filed_once_only_on_a_referral_and_only_when_it_holds` |
| F-61 | **A dispute could clear a case by subtraction** (BCH-T7-03, found in the 18 September live run). The Desk Investigator measured the progress notes byte-identical to the previous admission's and supported fraud at 0.90; the Medical Auditor opposed at 0.85; the Audit Reviewer disputed the desk. The claim, a fraud, was released. B-44 promised a disputed reading would leave the case buying field evidence or going to a human, and it did not | `aggregate_confidence` takes a disputed reading out of the confidence **denominator** as well as off its side, so striking the only dissent turned a split verdict into apparent unanimity: the surviving reading's share rose from 0.51 to 1.00 and the case's confidence from **0.46 to 0.85**, clearing the 0.70 floor it had been below. A reviewer that cannot clear a case could clear it by subtraction | **A dispute may set aside a reading, never a measurement** (B-44a). Where `ClaimStore.identical` itself finds a document byte-identical to another claim's, a dispute of a reading that supports fraud is refused and recorded (`disputes_refused`, shown in the artefact); the reading keeps its weight, and the case reaches exactly the decision it would have reached had the reviewer said nothing. A dispute of any other reading on the same case still applies. The reviewer's instructions no longer promise what a dispute does, and name the comparison as not theirs to dispute | `test_a_dispute_cannot_set_aside_a_reading_that_rests_on_a_measurement`, `test_the_refusal_guards_only_the_reading_the_measurement_backs`, `test_refusing_a_dispute_leaves_exactly_what_no_dispute_would_have_left`, `test_a_rules_run_records_no_refused_dispute` |
| F-62 | **Three questions no agent owned.** Nobody owned the money: BCH-R2-06, the one case *both* paths get wrong, is arithmetic - an implant invoice of Rs 12,000 against an excess of Rs 60,000, read as justifying it, because the rules desk only ever checked that an invoice was *present*. Nobody put the hospital's side before it was acted against, which is the part of NHA's process an automated desk drops most easily. And which questions a case raises was fixed by the trigger, though F-54 had already shown the roster wants deciding | The six-agent roster was drawn from the readings the evidence needed, not from the questions the process asks. The money looked like a document check because at the desk it is one; the hospital's side has no document to read, so nothing in the file demanded it | **Nine agents** (LLD B-47 to B-49): a **Billing & Tariff Analyst** computes the excess with `claim_tariff` and judges what the financial documents actually cover; a **Provider Advocate** puts the innocent account the file bears, and an account the evidence does not exclude turns a show-cause notice or a suspension into a field audit or a human review (`policy.defended`) and can **never** release a claim; a **Case Router** opens the optional tracks the file raises, holding no evidence tool and able only to **add** to the mandatory set. The billing reading is weighed and disputed like any other; the reviewer may dispute the advocacy, and a disputed defence never reaches the policy | `test_the_router_opens_work_no_rule_mandates`, `test_the_router_cannot_close_what_a_rule_opened`, `test_the_router_cannot_open_a_track_there_is_no_evidence_for`, `test_the_router_is_given_no_evidence_tool`, `test_an_unexcluded_defence_stops_an_action_no_human_has_reviewed`, `test_a_defence_the_reviewer_disputes_stops_nothing`, `test_a_defence_can_never_release_a_claim`, `test_a_rules_run_records_no_router_plan_and_no_defence` |
| F-63 | **An agent can return a well-formed answer that nothing supports.** Seen live: on scenario S8 the Audit Reviewer's summary read "the desk audit is disputed because ..." while its `disputes` list was empty, so the artefact printed "No finding disputed" beside a summary saying the opposite and nothing was set aside. The same shape of gap ran through the roster: a stance could cite nothing, the Billing & Tariff Analyst could state an excess it never computed, and a defence that stops an action could rest on a summary the advocate never checked against the file | Every guardrail tested an answer's **shape** - that it parsed, that it named a finding on the case, that a cross-claim stance had examined the other claims. Nothing tested whether the answer had been **earned**, and the policy downstream cannot tell an earned finding from an unearned one: both arrive as a stance and a confidence | **Five substance checks** (LLD B-50). A stance needs a citation. A stance on the money needs `claim_tariff` called first. A defence answering `excluded=false` needs the advocate to have read the file with a tool. The reviewer's summary may not assert a dispute its list omits, and negations ("no finding is disputed") are not assertions. The Case Router's **reasons** now reach the Decision and the artefact, so a widened roster can be audited on why, not only on what. Each agent's instructions state the requirement, so it is met first time rather than on the retry | `test_a_stance_without_a_citation_goes_back_to_the_agent`, `test_the_billing_analyst_cannot_state_an_excess_it_never_computed`, `test_a_billing_stance_stands_once_the_tariff_is_computed`, `test_a_defence_that_would_stop_an_action_must_have_read_the_file`, `test_the_reviewer_cannot_claim_a_dispute_its_list_does_not_contain`, `test_a_review_that_disputes_nothing_and_says_so_passes`, `test_the_artefact_says_what_the_router_added_and_why` |
| F-59 | **An officer's failed attempts left no trace** (BCH-R2-04: many model requests, one tool call in the trail, the decision executed by the orchestrator) | arguments that do not fit a tool are rejected by CrewAI before the tool runs, and repeated no-argument calls were answered from CrewAI's cache | every tool call runs (no cache), and a call rejected for its arguments is recorded as refused, with its arguments' shape | `test_arguments_that_do_not_fit_a_tool_are_recorded_as_refused`, `test_no_tool_answers_from_a_cache` |

**The agent benchmark, first live run** (17 September; 10-AGENT-EVALUATION; `gemini-3.5-flash-lite`). On 40 cases
whose truth is in the documents, the rules made **15 wrong decisions**. The crew made **5**, and it was right on every
paraphrase, mislabelled and cross-claim case. It compared the other admissions on all six T7 cases and told copied
records from distinct episodes on each. Its five errors, reported as found:
- **Three were keyword cases the rules got right, each tracing to a defect of ours, now fixed:**
  - BCH-T2-02 filed a histopathology report impossibly soon after surgery, and the crew was right to doubt it;
  - BCH-R2-07 and BCH-R3-06 had no invoice and no referral, which the crew read as upload slips (F-58).
- **Two were genuine misreadings, where the rules were wrong too:** a "referral" from a private clinic (R3-04), and an
  invoice covering a fifth of the excess (R2-06). They were deliberately not tuned away.

By accident, two processes decided 16 cases twice on the same code, and they agreed on all 16.

That first run is the **two-agent** crew's. 10-AGENT-EVALUATION keeps the runs side by side and says which crew
each is.

**The agent benchmark, hardened nine-agent run** (18 September, after the F-63 substance guardrails;
`gemini-3.8-flash` served 39 of 40 cases, one falling to `gemini-3.5-flash-lite` through a transient 503 and back).
**0 wrong decisions on the 39 cases the crew ran**, against the rules' 15. This is the run that tests the
guardrails rather than the score, because the benchmark had already saturated at zero wrong.

- **No guardrail forced a case to degrade.** This was the risk the hardening carried: an agent that cannot satisfy
  a guard twice stops the crew. Every agent met the new requirements, which is what stating them in each agent's
  instructions was for.
- **Cost, honestly: 20.1 model requests a case (782 for the run), up from 17.5.** Early in the run the figure sat
  *below* the old baseline, and had it been reported then it would have been wrong: the cheap T4 block flattered
  it, and the R2 and T7 blocks, where the `claim_tariff` and cross-claim guards actually bite, carried it above.
- The **Case Router** opened work no rule mandated on **10** cases, and for the first time across more than one
  track: billing 5, advocate 4, medical 1.
- The **Billing & Tariff Analyst** worked 12 cases and satisfied the `claim_tariff` guard on every one.
  **BCH-R2-06 was read correctly for the third consecutive run.**
- The **Provider Advocate** worked 22 cases and found an unexcluded innocent account on **2**, both on claims
  already being released, so it **again changed no decision**. The read-the-file guard raised the bar for an
  unexcluded defence, and fewer met it (2, against 3 before). The agent's restraint is demonstrated; its value is
  not, and this benchmark cannot demonstrate it, having no case where a defence ought to bite.
- The **Audit Reviewer disputed nothing for the third consecutive live run**, so B-44a's refusal rule has still
  never fired outside its tests. A guard proven by test that live conditions keep not reaching is worth stating
  plainly rather than counting as validated.
- **One case degraded** (BCH-R3-02, mislabelled evidence on an innocent claim): the model failed, the rules decided
  alone, and **the rules got it wrong** — a show-cause notice against an innocent hospital. It is excluded from the
  crew's score, correctly, since it is not the crew's decision. It is also the clearest single illustration of why
  the crew exists: the fallback path is the rules, and the rules are what the crew is measured against.

**The agent benchmark, nine-agent run** (18 September; `gemini-3.8-flash` served all 40 cases with no fallback). The
crew made **0 wrong decisions** on the 39 cases it ran, against the rules' 15; one case degraded (BCH-T7-06, an empty
response from the model) and is excluded from its score. No wrong decision in any of the six readings, and no innocent
claim enforced against.

**The confound, stated rather than buried.** The two earlier crew runs were served by `gemini-3.5-flash-lite`, because
the free tier's daily cap on the primary model was reached; the two later ones by `gemini-3.8-flash`. The fall from
two wrong to none therefore carries a model change inside it and cannot be credited to F-61, to the instructions, or
to the roster. The only model-controlled comparison available is six agents against nine on `gemini-3.8-flash`, and it
is unchanged: **zero wrong either way**. On these 40 cases the nine-agent crew held the result rather than improved
it, and the benchmark is now saturated for the crew — it can no longer discriminate between rosters.

What the new agents did, on the 39 scored cases:
- The **Case Router** opened work no rule mandated on **11** cases (billing 5, advocate 6). The branch is real: those
  agents would not otherwise have worked those cases, and the router never removed anything the rules mandated.
- The **Billing & Tariff Analyst** worked **12** cases (7 mandatory on R2, 5 opened by the router) and read the money
  correctly on every above-rate claim, including **BCH-R2-06**, the case both paths had got wrong in every earlier run
  (an implant invoice of Rs 12,000 against an excess of Rs 60,000). Its stance was `supports` at 0.95 to 1.00 on the
  four frauds and `opposes` at 0.95 on the three innocent ones.
- The **Provider Advocate** worked **23** cases and found an unexcluded innocent explanation on **3** — BCH-R3-01 to
  03, all innocent claims already being released. Because `policy.defended` touches only SHOW_CAUSE and SUSPEND, the
  defence **changed no decision in this run**. That the guard holds is shown; that the agent earns its cost is not,
  and this benchmark cannot show it, having no case where a defence ought to bite.
- The **Audit Reviewer disputed nothing**, so F-61's refusal rule was never exercised live in this run either. It is
  proven by test (`test_a_dispute_cannot_set_aside_a_reading_that_rests_on_a_measurement`), not by the benchmark.
- Cost: **17.5 model requests a case**, 683 for the run, against 5.4 for the two-agent crew and 13.4 for six agents.

**The agent benchmark, six-agent run** (18 September; `gemini-3.5-flash-lite`, which served all 40 cases after the
primary returned 503). The six-agent crew (F-60) on the corrected code and the corrected cases: **2 wrong decisions**
against the rules' 15 and the two-agent crew's 5. All 40 cases ran on the model; none degraded. It made no wrong
decision on the keyword, paraphrase, trap or mislabelled readings, and **no innocent claim was enforced against at
all** (the two-agent crew enforced against one). The Medical Auditor worked all 27 clinical cases, the Audit Reviewer
disputed a reading on 3, and the Enforcement Officer executed the decision on all 40; 8.8 model requests a case on
average, 19 at most, and no tool call was refused. Reported in both directions:
- **Four errors the two-agent crew made are gone.** BCH-R2-07 was fixed by F-58. BCH-R3-06 no longer releases, though
  it defers to a human rather than acting (acceptable, not correct). BCH-R3-04 — recorded above as a genuine
  misreading — the six-agent crew now reads correctly. BCH-T2-02 is correct for a reason worth recording: the case
  correction removed the impossible histopathology date, but the Medical Auditor then objected to the same-day
  discharge after a total thyroidectomy instead, and it was the Audit Reviewer disputing *that* reading which released
  the claim. The case is right, and not by the route the correction intended.
- **One error is new: BCH-T7-03, a cross-claim case the two-agent crew got right.** The Desk Investigator compared the
  admissions, found the progress notes byte-identical to the earlier admission's and said so; the Audit Reviewer
  disputed that reading, which by design makes it weigh nothing, leaving only the Medical Auditor's finding of
  clinically distinct episodes, and the claim was released. This is the dispute-only reviewer (B-44) removing a
  correct finding — the cost of a power that also cut two mistaken readings in the same run. Left as found.
- **BCH-R2-06 is still wrong**, as it was in the first run and on the rules path: an implant invoice covering a fifth
  of the excess, read as justifying it. Deliberately not tuned away.
- **Two cases the two-agent crew called outright are now deferrals** (BCH-T4-06 to a field audit, BCH-T2-03 to a field
  audit): the confidence floor buying evidence instead of guessing. A deferral is scored costly or acceptable, never
  correct, so the crew's 35 correct decisions are unchanged while its wrong ones fell from 5 to 2.


**Live on Gemini, 18 September, nine agents** (`python -m crew.run --scenarios --llm --provider gemini`;
`gemini-3.8-flash` served every case, no fallback). **9 of 10** produced the expected action, and the roster varied by
case: S1 router-desk-advocate-reviewer-officer, S2 and S5 adding the Committee Liaison, S5 seven agents on the
de-listing, S8 eight. The officer executed the decision on all eight cases the crew ran; S9 degraded by design.

**S8 did not match, and the reason is worth recording.** The scenario expects `no_action_review` (its two field
reports disagree, so a human should settle it). This run the Medical Auditor read the record as clinically
unjustified at 0.95 - mild acute bronchitis with preserved vitals, admitted anyway - which, against one field report
supporting and one opposing, carried the aggregate to **0.71**, a hundredth above the 0.70 floor, and the policy
issued a show-cause notice. Nothing structural changed: no finding was disputed, the advocate's defence did not
stand, and the same scenario passes on the rules path and on the scripted model offline. It is the live variance
09-EVALUATION and 10-AGENT-EVALUATION both warn about, landing either side of a threshold. **Check the table before
recording the demo.**

The Provider Advocate's first real opportunity to change a decision was this case, and it declined it: it built the
innocent explanation (three distinct acute illnesses) and then reported that the evidence excludes it, because the
beneficiary's own testimony records a single admission that month. An advocate that had argued otherwise would have
turned a notice into a human review on a case the field evidence says is a phantom claim.

**Live on Gemini, 17 September** (`python -m crew.run --scenarios --llm --provider gemini`; the primary returned 503
and then 429, and `gemini-3.5-flash-lite` served both runs):

| Run | Result | What the crew did |
|---|---|---|
| 1 (before F-55, F-56) | **8/10**: S3 and S7 failed as above | The officer executed the policy's decision on all 8 cases the crew ran. The investigator called tools unprompted on S1 (history, shared documents, two reads), S5 (shared documents, read, compare), S6 (surgeon's claims, read, compare), S7 and S8 (history, compares) |
| 2 (after) | **10/10** | The same, plus S3 released and S7 ordered to the field. S7: the investigator compared the admissions, found distinct episodes, and was blocked from clearing by the missing reports; the officer wrote two case-specific questions for the beneficiary call. S9 degraded as designed |

**Seen live, and consistent with the design:** a same-day hernia repair with a LAMA form was released at 0.95; the
escalation brief for S2 said the hospital "is not suspended at this time" and named cardiology and Gonda, 83 km,
exactly as `access_impact` states them. The crew ran on the backup model because the primary was rate-limited; the
runner says so.

### Found by the front end's own audit (23 September 2026)

The viewer introduced a second way to be wrong. Everything above is a wrong decision; these are a
correct decision **described** wrongly, which for a record is the same failure. Both were found by
reading the export against the page rather than by either one alone.

| # | Observed | Cause | Fix | Test |
|---|---|---|---|---|
| F-64 | **The benchmark page scored a case the crew never decided.** `/benchmark` opens on the nine-agent run, whose headline reads "39 of 40 scored, 1 degraded" with **0 wrong**. The table below it printed BCH-R3-02 with a red **wrong** badge, so the page contradicted itself on one screen, and credited the crew with a decision the rules had made | `metrics/benchmark.py` scores `not (crew and degraded)` and leaves a degraded row out of the totals, but the export still carries that row's `score` and the table printed whatever score it found. The page's fine print claimed a degraded case is "never shown as a pass" - it was shown as a *fail*, which is worse | The table applies the predicate the totals apply: on a crew path a degraded row renders **not scored**, with the reason on hover; on the rules path, where no model runs and every row is degraded, every row keeps its score. The fine print now states what is done | `prints no score for a degraded case on a crew path`, `shows exactly as many wrong badges as the headline reports`, `still scores every case on the rules path, where degraded is the normal state`, `leaves a crew path's degraded cases out of its totals`, `scores every case on the rules path, where degraded is the normal state` |
| F-65 | **`RUN-DEMO.bat /smoke` could not fail.** The smoke test fetches four pages and checks each carries a phrase only a prerendered build would hold. On a miss it printed `FAIL`, then printed "Smoke test done. The demo is good to click." and exited 0 | `:check_page` reported the failure to the screen and nowhere else - no flag, no exit code - and every path through the file ended `exit /b 0`. A stale or broken build passed the check that exists precisely to be run before the demo is trusted | The failure is recorded rather than only printed, the verdict is separated from the checks, and every failing path (no free port, no answer from the server, a failed build, no runtime) returns non-zero | Exercised by pointing one check at a phrase no page carries: the run printed `FAIL /benchmark/`, then `SMOKE TEST FAILED`, and exited **1**; restored, it exits **0** |

**Also checked and found sound**, against the claims made for them: the exporter is deterministic
(re-running it reproduces `cases.json`, `evaluation.json`, `benchmark.json` and `types.ts` byte for
byte, `meta.json` differing only in its timestamp); `types.ts` on disk is exactly what the exporter
would write, so it has not been hand-edited; the bundled map rebuilds byte-identically from its
pinned sources; every district and state the record names exists in that map; the threshold slider
reads a swept row at every stop rather than interpolating; and every asset every built page
references resolves over HTTP, including the module entry on the nested case routes, whose absence
would leave the pages looking complete and silently unhydrated.

## 6. Test data isolation

| Rule | Why |
|---|---|
| Fixed seed for every scenario | AC-7 reproducibility, and the recording must match a re-run |
| Scenario claims live in `tests/fixtures/`, not the bulk corpus | Hand-constructed, individually reviewable |
| No scenario references a real hospital name | EC-1 |
| District codes in fixtures are **real** | The adequacy lookup must hit real data |

The last row matters. Pseudonymise the *provider*, never the *district* — the district is what makes
the gate meaningful, and it is published fact.

---

## 7. Demo recording checklist

The demo recording must show:

- [ ] One case resolving **automatically** end to end (T1)
- [ ] One case **escalating**, with the access brief on screen (T2)
- [ ] One case being **refused** (T10)
- [ ] The **decision log** with `channels_used` differing across rows
- [ ] A run with **no LLM key**, completing in degraded mode (T9)
- [ ] The **investigation trail** in an artefact: the Desk Investigator listing and comparing S7's admissions, the Medical Auditor reading the other episodes' records, and the Enforcement Officer executing through its action tool
- [ ] The **agents line and the audit review** in an artefact: which agents worked the case, and whether the Audit Reviewer disputed a finding
- [ ] The **Committee Liaison's brief** in the S2 escalation artefact: the options open to the Committee and the recommendation
- [ ] A **refused** tool call: the officer offered the wrong action tool, or a draft or brief sent back (the offline tests script all three; a live run shows them only if the model errs)

Record from a seeded run so the recording matches what anyone re-running the code will see.

---

## 8. What we are not claiming

State this in the results section, unprompted:

> We claim no novel fraud detection. Triggers are NHA's published rules and are satisfied by
> construction on a simulated corpus. What these tests establish is that the **decision policy**
> behaves correctly and differently across 785 real district contexts, that the investigation path
> branches on the trigger as NHA's guidebook specifies, and that the system degrades and refuses
> rather than guessing. The false-positive rate reported is against our own clean decoys, not
> against real claims.

Volunteering this costs nothing — detection was never the contribution — and it removes the single
most likely line of attack in review.
