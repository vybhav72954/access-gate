# 06 · Risk Register

**Review cadence:** at the two hard gates — design sign-off (day 2) and build freeze (day 10).
**Scoring:** Likelihood × Impact, each 1–3. Anything scoring ≥ 6 needs an owner and a dated action.

---

## 1. Register

| ID | Risk | L | I | Score | Owner | Mitigation |
|---|---|---|---|---|---|---|
| **R-01** | **Auto-resolution share too low — the system reads as a triage tool in costume** | 2 | 3 | **6** | Policy | Gate on the *conflict*, not on thinness (DD-1). Measure the share from the first run, not at the end. If it falls below a majority, loosen `SEVERITY_SUSPEND` before loosening the access gate. |
| **R-02** | **Agents narrate rather than investigate — CrewAI becomes decoration.** *Realised in the first build and fixed on 17 September (F-54).* | 2 | 3 | **6** | Crew eng. | Nine agents, each owning one question no rule can answer, with the tools that question needs: a Case Router opens the optional work, four read the evidence independently, a Provider Advocate puts the hospital's side, an Audit Reviewer may dispute a reading that does not hold, and the Committee Liaison and the Enforcement Officer act on the decision through gated tools. Task guardrails require cross-claim evidence before a stance on T5 to T7. The policy decides between them. Every artefact carries the investigation trail and the agents that worked the case. The benchmark measures what the agents add, and where they are wrong ([10-AGENT-EVALUATION](10-AGENT-EVALUATION.md)). |
| **R-03** | Synthetic claims objection — "you found your own traps" | 3 | 2 | **6** | Argument | Volunteer it (Test Plan §8). Publish the generator and planted patterns. Include clean decoys so false positives are measured. Lead with R1, the check that runs on real data alone. |
| **R-04** | Capability flag fires on Punjab/Gujarat data convention | 3 | 2 | **6** | Crew eng. | `BULK_STATES` branch in `capability_flag` returns `state_convention=True`. Already specified in LLD §5.1. **Already caught — keep it caught.** |
| **R-05** | A real hospital is named in a fraud scenario | 2 | 3 | **6** | Validation | EC-1. Pydantic validator on `hospital_ref`; AC-8 grep test over all artefacts. |
| **R-06** | Quoting an individual district that turns out to be a vintage artefact | 3 | 2 | **6** | Problem | Check `is_post2011_district` before any district appears on a slide. Bahraich and Ahmedabad are both verified safe. |
| **R-07** | The team cannot defend the code under questioning | 2 | 3 | **6** | All | One government source per person. Policy owner defends every constant aloud in the day-12 rehearsal. |
| R-08 | Scope creep into the front-end | 3 | 2 | 6 | Comms | Not required. Timeboxed, read-only, built last, cut first. |
| R-09 | LLM cost or rate limits during testing | 2 | 2 | 4 | Crew eng. | Gemini free tier for development and the demo; calls paced to 10/min, 429s retried on the server's schedule, spent quota degrades at once. The 5,000-case evaluation makes no LLM calls. Deterministic path always available. |
| R-16 | The final presentation runs on a different model from the demo | 2 | 2 | 4 | Crew eng. | Decisions come from `policy.decide()`, not the model, so actions cannot change; findings and prose can. Re-run `--scenarios --llm --provider claude` before recording, and check the `model` column in the log. |
| R-17 | Free-tier Gemini prompts may be used by Google to improve its products | 2 | 1 | 2 | Argument | Case files contain only simulated claims, pseudonymous hospital IDs and public district data (EC-1). State it in the ethics section. Claude for the final presentation. |
| R-18 | **A pseudonym does not hide a hospital that network facts single out.** S2's escalation says its hospital is the only cardiology provider in Bahraich, which points to one real hospital. The `HOSP-nnnnn` mapping can also be recomputed, because it is a seeded shuffle of the public export | 2 | 3 | 6 | Validation | Say it before a panel does. The cases are simulated and every artefact says so ("no real hospital is alleged to have done anything"). The network facts are the point of the project, and they are public. Keeping the seed secret would break the byte-identical rebuild that proves the data. Never pair a scenario's conduct with a named hospital on a slide, and never publish artefacts outside the report. |
| R-10 | Inconsistent figures between report, deck and code | 2 | 3 | **6** | Validation | Every quoted figure traces to [04-DATA-DICTIONARY](04-DATA-DICTIONARY.md). Apply `EMPANELMENT_SCOPE` and `EXCLUDE_STATES` identically everywhere. |
| ~~R-11~~ | ~~Enforcement figures sourced only to news reporting~~ | — | — | **CLOSED** | Problem | Primary PIB releases obtained (PRID 2157879, 2099542, 1847423) and captured in `rulebooks/parliament/`. See [PRIMARY_SOURCES](../rulebooks/PRIMARY_SOURCES.md). |
| R-12 | CrewAI version churn breaking the build mid-week | 1 | 3 | 3 | Crew eng. | Pin `crewai==1.15.20`. Lockfile committed. Verified on 3.12 and 3.13. |
| R-13 | District crosswalk mismatches silently drop hospitals | 1 | 3 | 3 | Crew eng. | 99.3% placement is measured; assert the rate at load and fail loudly below 99%. |
| R-14 | Demo recording does not match a re-run | 2 | 2 | 4 | Comms | Record from a seeded run. AC-7 asserts byte-identical logs. |
| R-15 | Reuse of prior project data is challenged | 1 | 3 | 3 | Argument | The district frame is prior *competition* work, not graded coursework. Disclose in the brief and confirm at sign-off on day 2. |

---

## 2. The two that actually decide the outcome

### R-01 · Auto-resolution share

This is the risk that most plausibly sinks the project, because it attacks the Path A framing
directly, and it is the first thing a reader will probe.

**Why it happens:** adequacy alone flags 30.7% of cells as thin (≤2 providers). If thinness escalates,
roughly two in five cases go to a human and the system is a triage tool, not an automation.

**The fix is structural, not a tuning knob.** The gate fires only on a confirmed egregious finding
**and** a hospital that is its district's only real provider of some specialty it is empanelled for (no
alternative within 50 km, or one of two in a NITI aspirational district). Thin-but-not-sole suspends
automatically. Compliant hospitals in thin districts clear automatically — you *want* them there.

**Measure from day 5, not day 10.** Add the share to the runner's summary output so it is visible on
every run.

### R-02 · Agents as decoration

The honest failure mode of every CrewAI project. If the trigger layer is deterministic and the
agents merely restate what it found, the framework is a costume.

**Test for it, don't assert against it.** T6 and T7 assert that different triggers produce different
`channels_used`. If those assertions cannot be made to pass without hard-coding, that is real
evidence the crew is not doing work — and the right response is to say so in the report, not to
hard-code them.

The defensible position is narrow and worth stating precisely: NHA's guidebook prints a different
verification checklist per trigger across four evidence channels. The agents choose the path and
interpret conflicting evidence; they do not detect and they do not decide. Detection is
deterministic, and the decision is a pure function.

**Status, 17–18 September: it happened, it has been fixed, and the fix has been widened twice.** In the first build
no agent had a tool, since the
orchestrator called every tool itself (LLD B-3, "agent-callable tools after live testing", never revisited). The
triage agent's plan was never read, and the registry agent was always overridden by the lookup. The 5,000-case
evaluation never ran the crew. Only the desk agent changed decisions. The mitigation above did not catch it,
because asserting on `channels_used` tests the pipeline, not the agents.

The rebuild (LLD B-32 to B-39, §6a):
- **Desk Investigator:** five evidence tools. It decides what to fetch, and the guidebook's cross-claim desk checks
  are now possible.
- **Enforcement Officer:** tools to read the decision and the access impact, and seven action tools, each of which
  executes only the policy's decision.
- **Between them:** the policy decides.
- **Removed:** the triage and registry agents.
- **Checks:** a task guardrail sends back a stance on repeat admissions taken without looking at them. A rejected
  draft goes back to the officer with its reasons.
- **Measurement:** a 40-case benchmark whose truth sits in free text runs through both paths.

**Then the opposite failure was raised, and fixed too (F-60, LLD B-42 to B-46).** Two agents meant one reading of the
file, taken by one agent and checked by nobody. That is not how an anti-fraud unit works, and it wastes what agents are
for. The crew went to six: three independent readings — documents (Desk Investigator), clinical need (Medical Auditor,
on T2, T3, T4 and T7), the weight of each field report (Field Evidence Analyst); a review before anything is decided
(Audit Reviewer, which may only dispute); and a brief for the humans who decide (Committee Liaison), under the same
publication checks as a notice.

**And then the questions nobody owned (F-62, LLD B-47 to B-50), 18 September. The crew is now nine:**
- **The money** — the **Billing & Tariff Analyst** computes the rate, the claim and the excess with `claim_tariff` and
  reads what is supposed to account for it. BCH-R2-06, the one benchmark case *both* paths got wrong, was arithmetic:
  ₹12,000 of implant read as justifying a ₹60,000 excess.
- **The hospital's side** — the **Provider Advocate** puts the strongest innocent explanation the file will bear and
  says whether the evidence excludes it. This is the part of NHA's process an automated desk most easily drops.
- **Which questions the case raises** — the **Case Router** opens optional tracks the trigger does not mandate.

Each addition creates its own risk, and each is bounded in code rather than in instructions:

| The new risk | What bounds it |
|---|---|
| A router that decides what *not* to investigate is deciding the case | Its plan may only **widen**: `mandatory_tracks()` is a floor the model cannot lower (B-48). It gets **no evidence tool**, so it cannot form the reading its readers must reach independently |
| An advocate on a benchmark of 20 frauds would argue them free | The defence is **asymmetric**: it can turn a notice or a suspension into a field audit or a human review, and can never release a claim or stop an escalation (B-49). It must cite the file, and the reviewer may dispute it |
| A reviewer that cannot clear a case could clear it **by subtraction** | Realised, on BCH-T7-03: striking the only dissenting reading raised the survivor's confidence from 0.46 to 0.85 and released a fraud. A dispute of a reading resting on the claim store's own byte-identical comparison is now **refused and recorded** (F-61, B-44a) |
| More agents, more well-formed answers that nothing supports | Five guardrails on substance, not shape (F-63, B-50): a stance must cite; a money stance must have computed the figures; a defence that stops an action must have read the file; a review summary may not assert a dispute its list omits |

The risk all of this creates is still the mirror of R-02: agents added for appearance. The benchmark reports what each
one changed (10-AGENT-EVALUATION §2) — on the last measured run the router opened work on 10 of 39 cases and the
advocate found an unexcluded explanation on 2, neither of which changed a decision — and an agent that changes nothing should be removed, as triage and
registry were.

**Evidence the crew now works, not just runs.** On its first live run with tools, the crew caught two defects the
rules had carried since the fourth audit:
- **S3's package lacked two mandatory documents** (F-55). The investigator read the package master and asked for
  them.
- **The desk's mandatory-document check covered three of eight categories** (F-56). It cleared thin admissions whose
  investigation reports were never filed.

---

## 3. Issues already closed

Findings from the analysis phase, resolved before the build starts.

| ID | Finding | Resolution |
|---|---|---|
| C-01 | Delhi, Odisha, West Bengal appear as catastrophic network gaps | No official admission table through 2024-25 lists them; networks still being built in 2025 (73, 12, 43 hospitals). Excluded via `EXCLUDE_STATES`. Would have produced a false headline claim about West Bengal. |
| C-02 | Providers summed across specialties double-counted hospitals | Distinct-hospital count per district; per-specialty depth kept separate. Caught when one district exceeded its state's total. |
| C-03 | Newly created districts appear artificially thin | `is_post2011_district` flag; verify before quoting. |
| C-04 | OSM hospital counts unusable as a capacity denominator | Bahraich has 87 PM-JAY-empanelled hospitals against 30 OSM hospital points — 290% "empanelled". OSM undercounts Indian hospitals. Replacement-capacity feature dropped. |
| C-05 | CHC/PHC tertiary listings looked like mass misrepresentation | Punjab 94.9%, Gujarat 82.7%; top three states are 95% of cases. State convention, not fraud. Cannot be a trigger. |
| C-06 | "NHA never defines access" was too strong | NHA *does* name adequacy indicators — without thresholds, and only in planning. Claim narrowed and strengthened. |
| C-07 | Enforcement figures rested on news reporting | Primary PIB releases obtained. Also revealed two vintages — 1,114 de-empanelled (PIB) vs 2,359 (Lok Sabha, 31 May 2026). Enforcement roughly doubled; quote the later figure with its cut-off. |
| C-13 | **Specialty code vintages split provider counts** | `S12`/`MC` etc. counted as different specialties. Sole-provider share overstated (25.6% → 19.1%); the Gaya demo case was false — 3 cardiology providers, not 1. Canonicalised to 24 specialties; demo case now asserted in `build_reference.py`. Replaced by Bahraich. |
| C-11 | Retrospective sole-provider test attempted and **abandoned** | 41-hospital Haryana list; only 10 matched the registry, IDs differ, adequacy snapshot postdates removal. 14 cells cannot support a conclusion. Documented as not-reportable rather than published. |
| C-12 | CAG audit independently confirms four of our claims | R1 is an audited pattern with rupee figures; CAG measures access consequence directly; Bihar is worst in India at 1.8 EHCPs/lakh; physical verification skipped for 163 EHCPs. See `rulebooks/CAG_AUDIT_FINDINGS.md`. |
| C-09 | 30% of hospitals list no specialty at all | 9,265 of 30,858, across 285 districts. Registry specialty data is unreliable in both directions — absent here, overstated in Punjab/Gujarat. Reinforces "flag, don't conclude". |
| C-10 | `NA` (Paediatric Cancer) silently eaten by pandas | `pd.read_csv` maps `"NA"` to NaN, deleting 50 real cells. **We hit this ourselves** and caught it. Now asserted in `build_reference.py`; all consumers must use `keep_default_na=False`. |
| C-08 | Confirmed fraud base rate was unknown | PIB PRID 1847423: **0.18%** of authorised admissions. At a 1% FPR this means ~86% of flags are innocent — the arithmetic justification for the human checkpoint. |

| C-14 | Registry snapshot vintage unknown | Export dates are all `-NA-`. Validated against the Ministry's count for 1 March 2025: 31,196 vs 30,957 (+0.8%), 21 of 36 states exact. Described as an early-2025 network (D-8). |
| C-15 | **A death-register entry alone suspended hospitals** | Found by the evaluation corpus, not by the fixtures: every register error became a suspension. Desk now needs a certificate; otherwise field verification (B-10, E-1). |
| C-16 | **One field report could suspend a hospital** | Found by the evaluation corpus. Egregious triggers now order two independent channels; disagreement goes to a human (B-11, E-2). Measured before and after in 09-EVALUATION §6. |
| C-17 | Delhi shown with 0 PM-JAY hospitals in D-1 | Registry spells it `NCT OF Delhi`. Actual 73, matching the official count. Corrected; exclusion unchanged. |

| C-18 | **Live crew runs caught four of our own errors** | S3's fixture billed surgery the file said was deferred, and lacked the package's mandatory documents; S5 needed site verification before a de-listing referral; drafted notices overstated access loss and the action taken; the registry guardrail let an agent clear a claim. All fixed, and the fixes hardened in code: the rules now check mandatory documents, recorded facts outrank agent readings, and drafts are checked before they ship. See test plan F-06 to F-10. |

| C-19 | **The gate looked only at the billed specialty** | A suspension removes a hospital from every specialty. Found by the second code audit: 6.5% of confirmed egregious findings would have auto-suspended a district's only real provider of another specialty, more than the gate escalated. The gate now checks every empanelled specialty and the SEC pack names each one at stake (B-21, F-22). |
| C-20 | **The day-care list blinded two triggers** | A substring rule put 17 major burns surgeries ("follow-up dressings") and COPD ("opd") on the list, so zero-stay claims on them never fired. Rebuilt by script with a word-bounded rule; 24 packages removed (B-26, F-33). |

| C-21 | **Half the network could be accused of billing its own specialties** | 473 packages are listed under several specialties; reading one listing exposed 15,419 hospitals to false R1 notices (F-35). Every listing is now used. |
| C-22 | **Suspension on a reused document at the desk** | Team decision: a reused document waits for a hospital visit and a beneficiary call before any suspension (B-28). Clerical upload errors look like reuse at the desk. |

**C-04, C-05, C-15, C-16, C-18, C-19 and C-21 are worth telling in the presentation.** They are the difference between a team that
ran an analysis and a team that interrogated one.
