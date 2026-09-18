# 08 · Glossary

Domain terms used throughout the documentation. Everyone on the team should be able to define the
**bold** ones without looking.

---

## Scheme and institutions

**AB PM-JAY** — Ayushman Bharat Pradhan Mantri Jan Arogya Yojana. India's national public health
insurance scheme. Up to ₹5 lakh per family per year of cashless secondary and tertiary hospital care
for eligible low-income families.

**NHA** — National Health Authority. The apex body implementing PM-JAY nationally.

**SHA** — State Health Agency. The state-level implementing body. **Owns the enforcement decision
this project is about.**

**SEC** — State Empanelment Committee. Decides empanelment and de-empanelment. Cases close within 30
days of presentation. **This is our human checkpoint.**

**NAFU** — National Anti-Fraud Unit, at NHA. Sets fraud triggers, monitors nationally, withholds
flagged claims. Detection is already automated here.

**SAFU** — State Anti-Fraud Unit. Investigates flagged claims through desk audit, hospital visit,
beneficiary call and beneficiary visit.

**Trust mode / insurance mode / hybrid** — the three ways a state may implement PM-JAY: paying
claims directly from a state trust, through a contracted insurer, or a mix. Disciplinary authority
sits with the SHA and SEC in all three.

---

## Network and empanelment

**Empanelment** — the process by which a hospital is approved into the PM-JAY network and becomes
able to treat beneficiaries cashlessly and bill the state.

**EHCP** — Empanelled Health Care Provider. The term NHA's documents use for a hospital in the
network.

**De-empanelment** — removal from the network. **One year by default.** Accompanied by publication
of the hospital's name on NHA and SHA websites. The hospital continues to operate; it simply cannot
bill PM-JAY.

**Suspension** — temporary removal pending investigation. Existing inpatients complete their
treatment; new patients lose cashless access.

**Show-cause notice** — the formal notice giving a hospital **five days** to answer alleged
irregularities. Template in Annexure 4 of the anti-fraud guidebook.

**CHC** — Community Health Centre. A block-level public facility, typically 30 beds, offering basic
specialist services. **Not a tertiary-care facility.**

**PHC** — Primary Health Centre. The most basic tier of Indian public health infrastructure, below a
CHC.

---

## Clinical and billing

**HBP** — Health Benefit Package. NHA's master list of procedures PM-JAY covers, each with a package
code, specialty and rate. HBP 2022 is the current version.

**Package code** — the specific procedure billed, e.g. `MC012B`. Maps to a specialty and a tier; the first two
letters name its own specialty (`MC`, cardiology), and 473 packages are also listed under other specialties, any
of which a hospital may bill them through.

**Specialty code** — the registry field recording which specialties a hospital is empanelled for,
e.g. `S12` (Cardiology), `M1` (General Medicine), `S6` (Polytrauma). **89 codes**, read from the
`searchSpeciality` dropdown on the PM-JAY hospital search — the same system the registry export
comes from. 25 of them are OPD or diagnostic categories rather than inpatient specialties. See
[04-DATA-DICTIONARY §3](04-DATA-DICTIONARY.md#3-specialty-codes).

**LOS** — Length of Stay, in days. **Zero LOS on a major surgical package is NHA trigger 2** — the
patient was discharged the same day, so the billed surgery is implausible.

**Day-care package** — a procedure legitimately performed with same-day discharge. Zero LOS is
normal here, which is why the clean decoys in the test corpus include them.

**Unbundling** — billing several component procedures separately instead of the single package that
covers them, to inflate the claim.

**Upcoding** — billing a more expensive package than the procedure actually performed.

**DAMA / LAMA / DOR** — Discharge Against Medical Advice · Left Against Medical Advice · Discharge
on Request. Named in NHA's trigger guidance because they are used to explain implausibly short
stays.

---

## Geography and targeting

**LGD** — Local Government Directory. The government's canonical register of administrative units.
**LGD district codes are our join key**, because district names vary across sources and vintages.

**District vintage** — which set of districts a dataset reflects. India has created many new
districts since 2011; a source using older boundaries cannot be joined naively to one using current
ones. See [data defect D-2](04-DATA-DICTIONARY.md#d-2--district-vintage).

**NITI aspirational district** — one of 112 districts identified by NITI Aayog as most in need of
development. **Sole-provider rate is 27.0% here against 17.9% elsewhere** — the network is thinnest
where deprivation is already flagged. NHA's own empanelment guidelines annexe the list.

**Representative point** — the point used to locate a district for distance calculation. Chosen so
it falls inside irregular shapes, unlike a centroid.

---

## Project terms

**The access gate** — the check inserted before any suspension: *would suspending this hospital leave its
district without a real provider of any specialty it is empanelled for?* Suspension removes a hospital from all
its specialties, so every one is checked, not only the one billed. The project's contribution.

**Sole-provider cell** — a district × specialty combination with exactly one empanelled provider.
**19.1% of all occupied cells.**

**Phantom capability** — a facility listed for a specialty its type cannot plausibly deliver, such
as a PHC recorded as its district's only radiation oncology provider. The access it appears to
provide may not exist, so protecting it is protecting nothing — and its presence *masks* the gap.

**State convention** — the Punjab and Gujarat practice of bulk-empanelling basic facilities against
a broad package list. **Not fraud.** Those two states plus Telangana account for 95% of CHC/PHC
tertiary listings, which is why `capability_flag` must not treat this as a signal.

**Severity band** — 1 administrative, 2 substantive, 3 egregious. Only band 3 reaches the access
gate.

**Degraded** — a decision reached without the LLM, on deterministic rules alone. A designed path,
not a failure.

**Case Router** — the crew's first agent. It decides which of the optional investigations a case actually raises — the clinical, field, money or advocacy track — and opens them. It is given **no evidence tool**, and its plan may only *add* to the tracks the rules already mandate: a model deciding what not to investigate would be a model deciding the case.

**Track** — one optional line of investigation the crew can open on a case: `medical`, `field`, `billing`, `advocate`. `mandatory_tracks()` derives from the trigger and the file the ones the rules require; the Case Router may open more (`opened_by_router` in the decision log).

**Desk Investigator** — the agent that audits the documents the claim was paid on: present, consistent, genuine, and not copied from other claims. It fetches the claims related to this one (the beneficiary's other admissions, claims sharing a document, the surgeon's other claims that day) with its tools. It never sees network adequacy.

**Medical Auditor** — the agent that answers the clinical question on the triggers that turn on one (T2, T3, T4, T7): was the admission indicated, does the documented course justify the stay, was the procedure done, did each repeat episode have its own findings. It works without seeing the Desk Investigator's finding.

**Field Evidence Analyst** — the agent that weighs the field reports on file: how specific each is, whether it answers the question this trigger's field checks ask, whether it agrees with the documents. It may lower a report's confidence; it can never flip, inflate or drop what a field team recorded.

**Billing & Tariff Analyst** — the agent that answers whether the money is accounted for: the published package rate, the amount claimed, the excess, and whether the invoices, bills and implant records on file actually cover it. It must compute the figures with `claim_tariff` before taking any stance, because figures it did not compute are not figures the case establishes.

**Provider Advocate** — the agent that puts the hospital's side before it is acted against: the strongest innocent explanation the file will bear, and whether the evidence on file **excludes** it. It reads the other agents' findings and answers them.

**Defence** — the advocate's explanation, when the evidence does not exclude it. It can turn a show-cause notice or a suspension into a field audit, or into a human review when no field channel is left to order (`DEFENCE_UNEXCLUDED`). It can never release a claim, and it never stops an escalation or a de-listing referral, which already go to the Committee. Asymmetry is what makes it safe to give an agent a voice for one side.

**Audit Reviewer** — the agent that checks every finding against the evidence it cites before the policy decides, and **disputes** one that does not hold. A disputed finding is set aside and weighs nothing, so the case buys field evidence or goes to a human. It cannot convict or clear.

**Dispute** — the Audit Reviewer's only power: it sets a finding aside (stance null, confidence 0) and records the reason on the finding and in the decision log (`disputed`). It may dispute a reading or the advocacy, never a field report or the registry lookup.

**Refused dispute** — a dispute the rules reject and record (`disputes_refused`): one aimed at a reading that rests on the claim store's own byte-identical document comparison. Setting such a reading aside would take it out of the confidence denominator too, raising the surviving reading's confidence — clearing a case by subtraction, which a reviewer that cannot clear must not be able to do.

**Committee Liaison** — the agent that prepares the State Empanelment Committee when a case is referred to it: the evidence and the access consequence side by side, two to four options genuinely open to the Committee with their consequences, a recommendation, and the one question it must answer. It recommends; the Committee decides.

**Enforcement Officer** — the agent that carries out the decision. It reads the policy's decision and the access impact, explains the decision to the people it affects, and executes it through the one action tool that accepts it.

**Action tool** — a tool that executes one enforcement action (release, show-cause notice, field audit, suspension, escalation, de-listing referral, human review). It executes only when that action is the policy's decision, and only with an explanation that passes the publication checks; otherwise it refuses and says why.

**Investigation trail** — every tool the agents called on a case, in order, with refused calls marked. Printed in every artefact and summarised in the decision log (`tools_called`), beside the agents that worked the case (`agents`).

**Agent benchmark** — 40 hand-written cases whose truth is in the documents (paraphrase, keyword traps, mislabelled evidence, contradictions, copied records across admissions), run through the rules and the crew (10-AGENT-EVALUATION).

**Evidence channel** — one of NHA's four verification routes: desk audit, hospital visit,
beneficiary call, beneficiary visit. **Which channels are used depends on which trigger fired** —
this is the branching that justifies a crew.

---

## Delivery terms

**Path A** — agentic automation. The system decides *and acts*. Built with CrewAI or n8n. **Our
path.**

**Path B** — classical decision support. The system recommends; a human decides. Built with
Streamlit. Not our path — and note the distinction is precisely whether the system *acts*.

**Human-in-the-loop / HITL** — a checkpoint where a human must approve before the system proceeds.
Step 3a asks explicitly where, if anywhere, one sits. Ours is the access gate.

**Guardrail** — defined behaviour on edge cases, low-confidence outputs and errors. Step 3a requires
these. Ours are the confidence floor, the degraded path, and refusal on malformed input.
