# Primary Sources

Every figure quoted in the report, traced to a government source rather than to news reporting.
Raw captures are in `parliament/` so anyone can verify without re-fetching.

---

## 1. The base rate — and why it decides the design

> **"Around 0.18% of the total authorized hospital admissions under the scheme are confirmed as
> fraud since its inception."**
> — PIB, Ministry of Health and Family Welfare, *Anti-fraud system for India's National Health
> Insurance Scheme (AB-PMJAY)*, [PRID 1847423](https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1847423), 2 August 2022

This is the single most consequential number we found, and it reshapes the automation argument.

At a base rate of 0.18%, precision collapses under even a very good detector:

| Detector FPR | Recall | Precision | False flags per real fraud |
|---:|---:|---:|---:|
| 10.0% | 90% | 1.6% | 61.6 |
| 5.0% | 90% | 3.1% | 30.8 |
| 2.0% | 90% | 7.5% | 12.3 |
| **1.0%** | 90% | **14.0%** | **6.2** |
| 0.5% | 90% | 24.5% | 3.1 |
| 0.1% | 90% | 61.9% | 0.6 |

**At a 1% false-positive rate, roughly 86% of flagged claims are innocent.** To reach even 50%
precision the detector must hold below a 0.16% FPR — about one false flag in every 600 clean claims.

### Why this matters to the argument

It supplies the *arithmetic* reason for the boundary we drew on other grounds:

1. **Withholding a claim automatically is proportionate** — it is reversible, and being wrong six
   times out of seven costs a hospital a delay, not its existence.
2. **Acting against the hospital automatically is not.** At this base rate, an automated
   suspension pipeline would suspend mostly innocent hospitals. NHA's insistence on desk audit and
   field investigation before action is not bureaucratic caution; it is the only defensible response
   to the base rate.
3. **It justifies a high confidence floor.** `CONFIDENCE_FLOOR = 0.70` is not arbitrary — with a
   0.18% prior, low-confidence flags are overwhelmingly noise.
4. **It sharpens the access gate.** If most flags are false, then automatically suspending a sole
   provider risks removing a district's only cardiac hospital *over an error*. The gate is not
   squeamishness; it is the appropriate response to a low-prior detector with irreversible outputs.

Put this table in the deck. It converts "we escalate because the stakes are high" into
"we escalate because at a 0.18% prior, most of what we flag is wrong."

---

## 2. Enforcement figures

Two vintages. **Quote the later one with its cut-off date, and note the trajectory.**

### Current — Lok Sabha, as at 31 May 2026

Union Health Minister J. P. Nadda, written reply, Lok Sabha:

| Metric | Value |
|---|---|
| Hospitals de-empanelled | **2,359** |
| Hospitals suspended | **1,200+** |
| Penalties levied | **₹328.49 crore** |
| FIRs registered | **29** |
| Savings from automated checks (to 31 Jul 2026) | **₹676.14 crore** |

### Earlier — PIB, since inception

*Update on Strengthening of PM-JAY Implementation*,
[PRID 2157879](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2157879), and *Measures taken to
prevent misuse of AB-PMJAY Scheme*,
[PRID 2099542](https://pib.gov.in/PressReleaseIframePage.aspx?PRID=2099542):

> "A total of **3,167 hospitals were found guilty of irregularities/violations** since inception of
> the scheme, and suitable actions including **de-empanelment of 1114 hospitals**, levying
> **penalty worth Rs. 122 crores on 1504 errant hospitals** and **suspension of 549 hospitals**
> have been taken against fraudulent entities as reported by the States/UTs."

### The trajectory is itself a finding

| | De-empanelled | Suspended | Penalties |
|---|---|---|---|
| PIB, earlier vintage | 1,114 | 549 | ₹122 cr |
| Lok Sabha, 31 May 2026 | **2,359** | **1,200+** | **₹328.49 cr** |

**Enforcement roughly doubled between the two.** That makes the access question more urgent, not
less — the volume of network-removal decisions is rising, and none of them currently consider who
loses access.

---

## 3. Current process — confirmed from primary sources

From PRID 2099542 and 2157879:

- **NAFU** established at NHA, working with **SAFU** at state level, "to investigate and take joint
  action against issues related to fraud and abuse."
- Detection technologies named: **"rule-based triggers and Machine Learning algorithms, fuzzy logic,
  image classification and de-duplication."**
- Process: *"Such cases are checked through **desk audits, field investigation** subsequent to which
  appropriate action is taken including disabling of the Ayushman Cards, penalty, recovery or legal
  action…"*
- *"NHA has well-established audit mechanism and guidelines."*

This confirms, from the government's own releases, the two claims the project rests on: **detection
is automated, and action follows human audit.**

---

## 4. Scheme scale

For the problem brief. From PRID 2099542 and
[PRID 1996010](https://pib.gov.in/PressReleaseIframePage.aspx?PRID=1996010):

| Fact | Value |
|---|---|
| Cover | ₹5 lakh per family per year, secondary and tertiary hospitalisation |
| Eligible households, initial | 10.74 crore, from SECC 2011 deprivation criteria |
| Beneficiary base, revised Jan 2022 | **12 crore families** |
| ASHA / AWW / AWH families added, Mar 2024 | 37 lakh |
| Expansion, 29 Oct 2024 | **All senior citizens aged 70+**, irrespective of socio-economic status |
| Ayushman cards created | **30 crore** |
| Largest state by cards | Uttar Pradesh, 4.83 crore |

---

## 5. Rulebook corpus

| File | Source | Pages |
|---|---|---|
| `NHA_Empanelment_Guidelines_2021.pdf` | [nitiforstates.gov.in](https://www.nitiforstates.gov.in/public-assets/Policy/policy_files/GNC509Q000048.pdf) | 46 |
| `NHA_AntiFraud_Guidebook.pdf` | [cdnbbsr.s3waas.gov.in](https://cdnbbsr.s3waas.gov.in/s3169779d3852b32ce8b1a1724dbf5217d/uploads/2024/09/20240924831436164.pdf) | 164 |
| `NHA_Field_Investigation_Manual.pdf` | [sha.kerala.gov.in](https://sha.kerala.gov.in/wp-content/uploads/2026/03/NHA_Field-Investigation-and-Medical-Audit-Manual_April-2020.pdf) | — |
| `HBP_2022_package_master.pdf` | [nhmladakh.in](https://nhmladakh.in/HBP_2022.pdf) | 3,801 |
| `HBP_2.2_user_guidelines.pdf` | [hem.nha.gov.in](https://hem.nha.gov.in/HBP.pdf) | 64 |

### Key locations

| What | Where |
|---|---|
| Criteria relaxation clause — *"balance quality and access"* | Empanelment Guidelines **§3.2.3** |
| Minimum criteria floor | Empanelment Guidelines **Annexure 1** |
| Aspirational districts list | Empanelment Guidelines **Annexure 4** |
| Disciplinary ladder, 1-year de-empanelment | Empanelment Guidelines **§6.3** |
| Trigger-specific verification guidance, four channels | Anti-Fraud Guidebook **Annexure 2** |
| Show-cause / suspension / penalty / de-empanelment templates | Anti-Fraud Guidebook **Annexure 4** |
| LOS-based payment rules, LAMA/DAMA handling | HBP 2.2 User Guidelines |
| Implant reimbursement as separate add-ons | HBP 2.2 User Guidelines |

The HBP 2.2 guidelines are useful beyond the legend: they set out **what a legitimate claim looks
like** — payment by length of stay, documentation required per day, LAMA/DAMA treatment — which is
exactly what the desk-audit agent checks against.

---

## 6. What could not be obtained

| Wanted | Status |
|---|---|
| Hospital-level de-empanelment list | NHA publishes one in principle — §6.3.6.4 requires it be "prominently displayed" — but `nha.gov.in/img/resources/Hospital_De_Empanelment_Lit_261121.pdf` returns the portal's JavaScript shell. Aggregate counts only. |
| Lok Sabha answer PDFs from sansad.in | `sansad.in/getFile/loksabhaquestions/...` returns `{"errorCode":1001}`. Figures are quoted from reporting of the written replies; PIB releases give the earlier vintage directly. |
| HBP package rate list | Rate PDFs on `nha.gov.in` and `ayushmanup.in` return HTML shells. Punjab SHA hosts a live package-master viewer at `sha.punjab.gov.in/shapb/publicPages/packageMasterView.php` if rates become necessary. |
| Claim-level transaction data | Not public at any level. This is why claims are simulated. |

**Cite the de-empanelment count to the parliamentary answer, not to a list**, and say plainly that a
hospital-level list is published but not machine-retrievable.
