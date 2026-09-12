# 00 · Business Requirements Document

**Project:** The Access Gate
**Version:** 1.0
**Status:** Approved for build
**Owner:** Problem owner (see [07-TEAM-RACI](07-TEAM-RACI.md))

---

## 1. Executive summary

Ayushman Bharat PM-JAY provides ₹5 lakh a year of cashless hospital cover to India's poorest
families through a network of roughly 35,000 **empanelled** hospitals. Some of those hospitals bill
fraudulently. The National Health Authority already detects this automatically — but when it decides
what to *do* about a fraudulent hospital, it does not consider whether that hospital is the only
place in its district that performs the procedure.

**In 19.1% of district × specialty combinations, it is.**

This project builds an agentic system that investigates flagged claims, decides and acts
autonomously across the majority of cases, and escalates to a human specifically where enforcement
would remove a district's only source of a specialty — with the access impact quantified.

---

## 2. Business context

### 2.1 The scheme

| | |
|---|---|
| Programme | Ayushman Bharat Pradhan Mantri Jan Arogya Yojana (AB PM-JAY) |
| Cover | Up to ₹5 lakh per family per year, cashless, secondary and tertiary care |
| Network | ~35,286 empanelled hospitals across 725 district cells and 38 states/UTs |
| Delivery models | Trust mode, insurance mode, or hybrid — decided by each state |
| Non-participants | Delhi, Odisha and West Bengal have not implemented the scheme |

### 2.2 The fraud

Documented patterns, from NHA's own trigger catalogue:

- A major surgery billed where the patient was discharged the same day
- The same surgeon recorded operating in distant districts on one day
- Identical documents or images reused across different patients
- Admission, discharge or surgery recorded **after the patient's death**
- Medical management extending beyond ten days in non-critical cases

### 2.3 Scale of enforcement to date

| Metric | Value | Source |
|---|---|---|
| Hospitals found guilty of violations, since inception | 3,167 | PIB PRID 2157879 |
| Hospitals de-empanelled | **2,359** | Lok Sabha, as at 31 May 2026 |
| Hospitals suspended | **1,200+** | Lok Sabha, as at 31 May 2026 |
| Penalties imposed | **₹328.49 crore** | Lok Sabha, as at 31 May 2026 |
| FIRs registered | 29 | Lok Sabha, as at 31 May 2026 |
| Savings from automated checks | ₹676.14 crore | as at 31 Jul 2026 |
| **Confirmed fraud rate** | **0.18% of authorised admissions** | **PIB PRID 1847423** |

**Enforcement is accelerating.** PIB's earlier figures were 1,114 de-empanelled and 549 suspended
with ₹122 crore in penalties; the May 2026 parliamentary answer is roughly double on every measure.
The volume of network-removal decisions is rising, and none of them currently consider who loses
access.

**And the base rate decides the design.** At 0.18% confirmed fraud, a detector running at a 1%
false-positive rate produces flags that are ~86% innocent. That is the arithmetic reason NHA
withholds the claim automatically but requires human audit before acting against a hospital — and
it is why our gate sits where it does. Full working in
[PRIMARY_SOURCES §1](../rulebooks/PRIMARY_SOURCES.md#1-the-base-rate--and-why-it-decides-the-design).

---

## 3. Stakeholders

| Stakeholder | Role in the decision | Interest |
|---|---|---|
| **State Health Agency (SHA)** | Owns the enforcement decision. Issues notices, suspends, levies penalties. | **Primary user.** Wants throughput without indefensible calls. |
| **State Anti-Fraud Unit (SAFU)** | Investigates flagged claims — desk audit, hospital visit, beneficiary contact. | Wants a prioritised queue, not an undifferentiated pile. |
| **State Empanelment Committee (SEC)** | Decides de-empanelment. Closes cases within 30 days of presentation. | Wants a complete evidence pack, including consequences. |
| **National Anti-Fraud Unit (NAFU)** | Sets triggers, monitors nationally, withholds flagged claims. | Upstream. Supplies our input. |
| **Empanelled hospital** | Subject of the decision. Five days to answer a show-cause notice. | Due process; proportionate action. |
| **Beneficiary** | Bears the access consequence. Not consulted today. | **The unrepresented party. This project represents them.** |

---

## 4. Problem statement

> A State Health Agency must decide, thousands of times a year, what to do about a hospital its
> automated system has flagged for suspected claim fraud — and it currently makes that decision
> without knowing whether the hospital is the only place in its district that performs the procedure
> in question.

### 4.1 What happens today

Detection is automated; every step after it is human.

| Stage | Who | Clock | Automated |
|---|---|---|---|
| Trigger fires on a claim | NAFU platform | within 24 h | **Yes** |
| Claim withheld pending scrutiny | NAFU | immediate | **Yes** |
| Desk medical audit | SAFU | — | No |
| Hospital visit · beneficiary call · beneficiary visit | SAFU field team | — | No |
| Show-cause notice | SHA | 5 days to reply | No |
| Suspension pending investigation | SHA | stated months | No |
| Penalty | SHA | 7 working days to deposit | No |
| De-empanelment | **SEC** | closed within 30 days | No |
| Public naming · FIR | SHA | — | No |

De-empanelment runs **one year by default**. The only patient-protection clause anywhere in the
ladder is that existing inpatients finish their treatment. Nobody asks about the next patient.

### 4.2 The gap

NHA's *Guidelines on Hospital Empanelment and De-Empanelment* §3.2.3:

> "…based on their local context, **availability of providers, and the need to balance quality and
> access**, with prior approval from National Health Authority."

The same document lists adequacy indicators for the SEC to monitor — hospital-to-population ratio,
beds and doctors to population, *specialties in various districts*, *geographic distribution of
empanelled hospitals*, *percentage of available eligible hospitals in the district empanelled*.

**NHA names the variables, attaches no threshold to any of them, and sites them in empanelment
planning — never in the disciplinary process.** The enforcement ladder in §6.3 runs on fraud
severity alone.

---

## 5. Business objectives

| # | Objective | Measure of success |
|---|---|---|
| BO-1 | Reduce SAFU investigation time on clear-cut cases | Auto-resolution share of flagged claims |
| BO-2 | Prevent enforcement actions that silently remove a district's only provider | Zero auto-suspensions of sole real providers |
| BO-3 | Give the SEC a complete pack, including access consequence | Every escalation carries quantified impact |
| BO-4 | Make the enforcement threshold defensible rather than asserted | Threshold sweep published with operating point marked |
| BO-5 | Avoid systematic disadvantage to irreplaceable providers | Disparate-impact audit by hospital type, reported either way |

---

## 6. Scope

### 6.1 In scope

- Ingesting a flagged claim and its context
- Routing the investigation across NHA's four evidence channels according to which trigger fired
- Gathering and interpreting evidence, including contradictions between sources
- Deciding and **executing** the action: releasing the claim, withholding it, issuing a show-cause
  notice, ordering a field audit, or suspending
- Computing network adequacy and gating suspension on it
- Producing an escalation brief for the SEC where the gate fires
- A decision log for every case

### 6.2 Out of scope

| Excluded | Why |
|---|---|
| De-empanelment decisions | Reserved to the SEC by NHA policy |
| Criteria relaxation approval | Requires prior NHA approval by policy |
| Novel fraud detection | Triggers are NHA's published rules; we claim no detection contribution |
| Claim adjudication or pricing | Different decision, different owner |
| Real patient or claim data | Not public; simulated per Step 3a |
| Any front-end beyond a read-only queue viewer | Not required by Path A; timeboxed if built |

### 6.3 Assumptions

1. Claim-level data is unavailable and will be simulated against the real hospital network.
2. The registry's specialty field records *entitlement to claim*, not verified capability.
3. The district is the correct **decision** unit — it is where the committee sits and the registry
   is keyed — though it is an imperfect **catchment** unit.
4. An LLM endpoint is available; if not, the system degrades to deterministic rules.

---

## 7. Ethical constraints

**EC-1 · Pseudonymisation.** Provider identity is pseudonymised in every fraud scenario. The
registry contains real, named, identifiable hospitals and our outputs are accusatory. Real
identities are used only for network-structure computation, which is published fact. **No real
hospital is asserted to have committed any offence.**

**EC-2 · No training on simulated data.** Simulated data is used as *input* only, never as
*training* data. No component is fitted on generated claims.

**EC-3 · Flags, not conclusions.** The system's output on capability is *"flagged for verification,
ranked by access consequence"* — never *"confirmed incapable."* Capability cannot be established
without a site visit, and the real process knows this.

**EC-4 · Disparate impact is measured, not assumed away.** Not-for-profit hospitals are 4.1% of the
network but 7.1% of sole providers. A policy that resolves cleanly on well-documented cases
advantages chains with compliance departments. We audit for this and publish the result whichever
way it falls.

---

## 8. Success criteria

| ID | Criterion | Threshold |
|---|---|---|
| SC-1 | Pipeline runs end to end on all ten test scenarios | 10/10 produce the expected outcome |
| SC-2 | Outcomes are genuinely distinct | ≥ 6 distinct terminal actions across the ten |
| SC-3 | Branching is demonstrated, not asserted | Tests 6 and 7 invoke different evidence channels than Test 1 |
| SC-4 | The thesis is executable | Tests 1 and 2 differ **only** in district, and produce different actions |
| SC-5 | Path A holds | Auto-resolution share reported; majority resolved without a human |
| SC-6 | Degradation is designed | Test 9 completes with `degraded = true` and no crash |
| SC-7 | Every threshold is defensible | Each constant in `policy.py` carries a written reason |

---

## 9. Constraints

| Type | Constraint |
|---|---|
| Tooling | An agent framework was required. We use CrewAI 1.15.20. |
| Outputs | 1-page brief · 1–2 page argument · project files + recording · 5–10 test cases · deck |
| Team | Five members; every member must be able to defend every design choice |
| Data | Claim-level PM-JAY data is not public |
| Weighting | Reality 15% · Argument 25% · Build 30% · Testing 15% · Presentation 15% |

**Implication of the last row:** 55% of marks are reasoning, evidence and communication against 30%
for the build. Protect the argument and the tests before the build, and the build before any UI.

---

## 10. Traceability

| Business objective | Design element | Test |
|---|---|---|
| BO-1 | Auto-resolution branches in the decision table | T1, T3, T4 |
| BO-2 | The access gate (`district_adequacy` before suspension) | **T2** |
| BO-3 | Escalation brief artefact | T2, T5 |
| BO-4 | `DISTANCE_MATERIAL_KM` sweep | Reported metric |
| BO-5 | Disparate-impact audit | Reported metric |
| EC-1 | Pseudonymous `hospital_ref` in the claim schema | All |
| EC-3 | `capability_flag` returns `plausible`, not `capable` | T5 |
