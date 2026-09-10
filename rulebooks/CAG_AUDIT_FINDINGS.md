# CAG Performance Audit — what an independent auditor already found

**Source:** Comptroller and Auditor General of India, *Report No. 11 of 2023*, Chapter 4 —
Hospital Empanelment. Full text in `CAG_PMJAY_Performance_Audit_Ch4.pdf` / `.txt`.

This is the highest-value document in the project. It is an **independent statutory audit** of the
exact decision we are building around, and it independently confirms four things we had derived,
inferred or assumed.

---

## 1. Our R1 trigger is an audited fraud pattern, with rupee figures

We invented "package billed for a specialty the hospital is not empanelled for" as a detection that
runs on real data. **CAG audited precisely this and found it across states:**

| State | Finding | Amount |
|---|---|---|
| Assam | 18 EHCPs treated 1,149 beneficiaries for non-empanelled specialties | **₹1.27 crore** paid |
| Chhattisgarh | 65 EHCPs claimed packages they were not empanelled for | **₹0.29 crore** |
| Gujarat | **20 of 26** test-checked EHCPs provided non-empanelled specialty treatment | — |
| Jharkhand | Lifeline Nursing Home, Godda performed **92 Phaco procedures without a Phaco machine** | ₹5.98 lakh paid on 72 |

The governing rule, quoted by CAG: *"Empanelled EHCPs are allowed to provide treatment to the
beneficiaries only for those specialties for which they are empanelled."*

**Use this in the report.** R1 stops being a clever idea we had and becomes the automation of a
check a statutory auditor found is not being made.

---

## 2. CAG already measures the access consequence — our metric has precedent

We built an access measure because NHA's guidelines name the trade-off without defining it. CAG
went further and **counted the affected people**:

> **Haryana** — "14 specialties were not available in various Districts of State. Hence, **1,178
> PMJAY beneficiaries had to travel to another District/State** to avail the treatment."

> **Andaman & Nicobar Islands** — no super-speciality facilities at the 545-bed referral hospital or
> two district EHCPs. Of 316 hospitalisations, **34 patients were referred to Chennai, Kanchipuram,
> Kolkata and Madurai — nearly 1,500 km from Port Blair.**

> **Maharashtra** — 1,113 types of treatment not provided.

This is our thesis, audited. We are not introducing a novel concern; we are supplying the
**systematic, computable** version of a measurement CAG has already made case by case.

---

## 3. CAG uses an adequacy metric — and Bihar is the worst in India

> "The EHCPs availability per one lakh beneficiary ranged from **1.8 EHCPs in Bihar** to 26.6 in Goa."
> In Lakshadweep, 90.8 per lakh.

> "Though beneficiaries in **Bihar** and Uttar Pradesh are numerous at 5.56 crore and 6.47 crore,
> availability of EHCPs was very low in comparison at **1.8** and five EHCPs respectively to a lakh."

**Bahraich is in Uttar Pradesh — which CAG singled out alongside Bihar.** Our headline case sits in the state an independent auditor singled out as
having the thinnest network in the country — a 15× gap against Goa. Cite CAG alongside the Bahraich
figures and the case stops being one district we picked.

Note the metric differs from ours: CAG counts **EHCPs per lakh beneficiary**, we count **providers
per district × specialty**. CAG's is a coverage measure; ours is a substitutability measure. Say so,
and present ours as the finer-grained complement rather than a competitor.

---

## 4. Our provider counts are optimistic — for three audited reasons

We already knew the registry overstates capability in Punjab and Gujarat, and omits it entirely for
30% of hospitals. CAG adds three more reasons the *counts themselves* are too high:

### Non-functional hospitals

> **Andhra Pradesh** — of 1,421 empanelled EHCPs, **524 submitted zero claims** and 81 submitted one
> to five. *"This indicates that the EHCPs are not fully functional."*

**37% of one state's network submitted no claims at all.** A district recorded as having two
cardiology providers, one of which has never treated anyone, effectively has one.

Also: Jharkhand, 59 EHCPs never treated a patient since empanelment; one medical college provided no
treatment for 761 of 1,096 days. Punjab, five EHCPs no treatment up to March 2021 despite
empanelment in 2019–20. Tamil Nadu, none of 19 GOI EHCPs entertaining patients.

### Duplicate unique IDs

> **Tamil Nadu** — "57 EHCPs were allotted two or more unique ID."
> **Jharkhand** — one EHCP in Dhanbad and seven in Ranchi "empanelled twice by SHA with different
> identification, though locations of the EHCPs were same."

This independently confirms our own finding of **1,082 identical hospital-name collisions within a
district** affecting 2,248 hospital IDs. Some of our provider counts are double-counting one
facility.

### Physical verification not done

> "Physical verification was not conducted in **163 EHCPs** in Manipur (17), Tripura (103) and
> Uttarakhand (43). Empanelment without conduct of physical verification has the risk of empanelment
> of EHCPs which **do not fulfil minimum criteria** of empanelment."

The process that is supposed to establish capability was skipped for those hospitals. This is the
audited justification for **EC-3** — capability is a flag, never a conclusion.

**Net effect: every one of these pushes the same way.** Real substitutability is *worse* than our
19.1% suggests. The argument is conservative, and now conservative for six documented reasons rather
than two.

---

## 5. The minimum criteria, in CAG's own summary

Useful for the compliance agent — this is the General Criteria floor (HEM guidelines para 1.3):

- **At least 10 in-patient beds**
- Round-the-clock support for pharmacy, blood bank, laboratory, dialysis, ambulance
- 24-hour emergency services managed by technically qualified staff, where offered
- Fully equipped operation theatre
- Waste management support, general and bio-medical

CAG also records the expected empanelment pattern for basic facilities:

> "**PHCs are generally empanelled for Gynaecology and CHCs are empanelled for the Gynaecology,
> Paediatrics and General medicine specialties** by the SHA."

**That makes the Punjab/Gujarat pattern more anomalous, not less.** The expected CHC profile is
gynaecology, paediatrics and general medicine. Punjab and Gujarat list CHCs for cardiology and
neurosurgery.

---

## 6. Process facts worth citing

| Fact | Source |
|---|---|
| DEC processes an application **within 15 days** of receipt | §4.4 |
| DEC physically inspects and reports to SEC with pictures/videos/scans | §4.4 |
| Hospital found with unapplied-for available specialties must apply **within 7 days** or be disqualified | §4.5 |
| **26,209 hospitals empanelled** as of November 2022 (11,930 private, 14,279 public) | §4.1 |

Against our July 2026 registry of 35,286, the network grew roughly **35% in under four years** —
which is why enforcement volume is rising and the access question is getting harder, not easier.

---

## 7. How to use this

**In the problem brief.** The current process has been independently audited and found wanting on
exactly the dimension we address. That is a stronger opening than any statistic.

**In the automation argument, under *data quality*.** CAG documents that physical verification is
skipped, IDs are duplicated, and a third of one state's network is dormant. This is the evidence for
"good enough to rank and gate, not good enough to conclude."

**In the test results.** R1 is no longer a synthetic check — cite the Assam ₹1.27 crore and the
Godda Phaco machine as the real-world pattern the test reproduces.

**In the Q&A**, for *"why should we believe your access numbers?"*: because an independent statutory
auditor measured the same thing by hand in three states, and because six documented factors all push
our estimate in the conservative direction.

**One caution.** This is Report No. 11 of 2023, auditing a period around 2018–2021 with NHA replies
from August 2022. Some findings may have been remediated. Present it as *"the most recent
independent audit found…"* and note the date, rather than implying it describes today.
