# 01 · High-Level Design

**Project:** The Access Gate
**Version:** 1.0
**Audience:** Whole team
**Prerequisite:** [00-BRD](00-BRD.md)

---

## 1. Design principle

> **Automate up to the point where the action stops being reversible. Gate the rest on who loses
> access.**

Three consequences follow, and every design choice below traces to one of them:

1. **The enforcement ladder is not uniform**, so the automation verdict cannot be uniform either.
   Withholding a claim is reversible; publishing a hospital's name is not.
2. **Being pivotal is not a reason to involve a human.** A compliant hospital in a thin district is
   auto-cleared — you *want* it there. Only the *conflict* escalates: serious finding **and** a hospital
   whose suspension would leave its district without a real provider of some specialty. Suspension removes a
   hospital from every specialty it is empanelled for, so the gate checks them all, not only the one billed.
3. **Deterministic work belongs in tools, not agents.** Network adequacy is arithmetic. Putting it
   inside an agent would be theatre.

---

## 2. System context

The system sits between NAFU's national trigger engine and the State Health Agency's enforcement
actions. See [03-DIAGRAMS §1](03-DIAGRAMS.md#1-system-context).

| Actor / system | Direction | Interface |
|---|---|---|
| NAFU trigger platform | in | Flagged claim + trigger id |
| PM-JAY hospital registry | in | Real registry: type, district, specialties, contact |
| District feature frame | in | 785 districts × 93 indicators |
| District geometry | in | Representative points for distance |
| NHA rulebooks | in | Trigger guidance, criteria, letter templates |
| Empanelled hospital | out | Show-cause notice; claim withheld or released |
| SAFU field team | out | Field audit order |
| **State Empanelment Committee** | out | **Escalation brief with access impact** |

---

## 3. Component architecture

Five layers. Only one contains agents.

| Layer | Component | Responsibility | Deterministic? |
|---|---|---|---|
| **L1 Ingest** | `generate/fixtures.py`, `generate/corpus.py` | The ten test scenarios; a simulated year of flagged claims placed on the real network, for measurement | Yes |
| **L2 Detect** | `rules/triggers.py` | Evaluate NHA's published trigger rules; emit `TriggerHit` | Yes |
| **L3 Reason** | `crew/` | Nine agents with tools: one routes the case, four read the evidence independently, one puts the hospital's side, one reviews every finding, and two act on the decision the policy makes between them | **No — LLM** |
| **L4 Decide** | `rules/policy.py` | Apply thresholds and the decision table; the access gate | Yes |
| **L5 Act** | `crew/actions.py` | Execute: release, withhold, notice, audit order, suspend, escalate | Yes |

**Measurement** (`metrics/run.py`) is a harness outside the five layers: it drives the real pipeline over the corpus and writes `results/` and [09-EVALUATION](09-EVALUATION.md).

**Reference data** (`data/reference/`) is precomputed and read-only: district adequacy, access
distance, specialty legend. Lookups over it are tools, not agents: the policy reads adequacy directly, and the
Enforcement Officer and the Committee Liaison read it through `access_impact`, after the decision. No tool of the
agents that read the evidence reaches it.

---

## 4. The agent layer

Nine agents in one CrewAI crew (`crew/crew_llm.py`, configured in `crew/config/agents.yaml` and `tasks.yaml`). Each has
one question no rule can answer, and the tools that question needs. One routes the case, four read the evidence
independently, one puts the hospital's side, one reviews every finding, and two act on the decision the enforcement
policy makes between them.

| Agent | The question it owns | Tools | When it works | Why not a function |
|---|---|---|---|---|
| **Case Router** | Which of the optional investigations does this file actually raise? | none, by design | Every case, first | Which questions a case raises is a judgement about the case, and the rules can only key it to the trigger. It is given no evidence tool: a router that has read the file has already reached the readers' question. It may only **add** to the tracks the rules mandate (B-48) |
| **Desk Investigator** | Are the documents present, consistent, genuine, and not copied from other claims? | `beneficiary_claim_history`, `documents_shared_with_other_claims`, `surgeon_same_day_claims`, `read_document`, `compare_documents` | Every case | The evidence is free text, and copied vitals across three admissions only show when the admissions are set side by side. The agent decides which claims to fetch and what the comparison means |
| **Medical Auditor** | Was the care billed clinically needed, and does the course documented justify it? | `beneficiary_claim_history`, `read_document` | T2, T3, T4, T7: the triggers that turn on a clinical question | "Persistent hypoxia needing BiPAP" justifies a stay that no keyword list finds, and "comfortable, awaiting family" does not. It works blind to the desk's finding, so two readings of the same file are independent |
| **Field Evidence Analyst** | How much weight does each field report on file deserve? | `read_document` | When field reports are on file | A vague report answering a different question is weaker evidence than a specific one, and only reading both tells them apart. It may discount a report, never overturn it |
| **Billing & Tariff Analyst** | Is the amount claimed accounted for by the rate and the documents on file? | `claim_tariff`, `read_document`, `beneficiary_claim_history` | R2, and any claim above its package rate; the router may open it elsewhere | An invoice on file is not an explanation of an amount. Whether ₹12,000 of implant accounts for a ₹60,000 excess is arithmetic against free-text documents, and the rules desk only ever checked that a document was present |
| **Provider Advocate** | What innocent explanation will this file bear, and does the evidence exclude it? | `read_document`, `claim_tariff` | Whenever a reading supports the suspicion; the router may open it otherwise | Before a hospital is acted against, someone must put its side. An explanation the evidence does not exclude stops an action no human has reviewed; it never releases a claim, and the Audit Reviewer may dispute it like any other reading (B-49) |
| **Audit Reviewer** | Does each reading hold against the evidence it cites? | the five evidence tools | Every case, before the policy decides | The second pair of eyes anti-fraud work requires. It disputes a reading that does not follow from what it cites; a disputed reading weighs nothing, and what the case does next depends on the readings left standing. It may not set aside a reading that repeats a comparison the claim store makes itself (B-44a). It can neither convict nor clear |
| **Committee Liaison** | What must the State Empanelment Committee decide, and on what? | `enforcement_decision`, `access_impact`, `file_committee_brief` | On an escalation or a de-listing referral | The brief sets evidence against access and lays out the options genuinely open to the Committee. Writing it is the escalation; the Committee decides |
| **Enforcement Officer** | How is the decision executed and explained to the people it affects? | `enforcement_decision`, `access_impact`, seven action tools | Every case | The notice is generative, not a template fill. The officer acts only through tools that execute nothing but the policy's decision, so it cannot change it |

What the agents do not do, by design:
- **No agent that reads the evidence sees network adequacy.** Whether a hospital committed fraud must not depend on
  who would lose access if it did. Only the two agents that act on the decision can read the access facts.
- **The readers do not see one another's findings.** The four reading tasks take no context, so the desk, clinical,
  field-weight and money readings are four independent readings of one file. The Provider Advocate receives them (it
  answers them), and the Audit Reviewer receives all five.
- **No agent decides an action.** The readings pass through the evidence rules (`crew/guardrails.py`), and
  `rules/policy.py` decides, between the reviewer and the two agents that act.
- **The reviewer cannot decide either.** It disputes or it does not; a dispute sets a reading aside, and what follows
  from that is the policy's.
- **Registry verification is not an agent.** It is a lookup, and it enters the findings as one.

The first build had four agents with no tools. Triage's plan was never used, and the registry agent was always
overridden, so the crew was decoration. The rebuild to two agents that do real work, the widening to six, and the
widening to nine are in 02-LLD §0 (B-32 to B-37, B-42 to B-46, B-47 to B-49) and §6a.

### 4.1 Why a crew and not a script

NHA's guidebook gives every trigger its own verification checklist across all four evidence channels. The
investigation always starts at the desk, and **what happens next depends on what the desk finds**:
- settle it there (S1, S3);
- order specific field channels, because the documents cannot answer the question (S7);
- weigh the field reports already on file (S6, S8).

The same pipeline takes different paths through the evidence, and `channels_used` records which.

Three parts of that job are not deterministic, and they are the crew's:
- **Reading the evidence.** Documents state their facts in free text, and the guidebook's desk checks for repeat
  admissions (T7) and reused documents (T6) compare documents across claims. The documents' integrity, the clinical
  need, the weight of a field report and whether the money is accounted for are four different questions about the
  same file, which is why they are four agents. Each decides what to fetch. Every tool call is recorded in the
  decision's investigation trail.
- **Putting the hospital's side**, because an action decided without one is an audit that only ever looked one way.
- **Checking every reading before anything is decided**, which is what an audit does before a hospital is acted
  against.
- **Explaining the action to the people it affects, and executing it** — and, on an escalation, preparing the
  Committee to decide.

Detection is deterministic, and so is the decision. The benchmark in
[10-AGENT-EVALUATION](10-AGENT-EVALUATION.md) measures where the agents' reading beats the rules' patterns, and where
it does not. *(See 02-LLD §0, B-2 and B-32 to B-39.)*

---

## 5. The decision boundary

See [03-DIAGRAMS §5](03-DIAGRAMS.md#5-decision-boundary).

```
AUTOMATED ─ no human in the loop
  Trigger → Route → Gather → Interpret → Act
  (release · withhold · show-cause notice · field audit order · suspend where alternatives exist)

═══════════ THE ACCESS GATE ═══════════
  adequacy of every specialty the hospital provides, checked before any suspension

ESCALATED ─ State Empanelment Committee
  Brief → Weigh safety against access → Suspend or de-empanel
```

**Automated** actions are high-volume, reversible and explainable.
**Escalated** actions are irreversible, name a provider publicly, and remove access.

The boundary is not our judgement. NHA's guidelines reserve criteria relaxation for prior NHA
approval and de-empanelment for the SEC. We are implementing the client's policy and supplying the
number it lacks.

---

## 6. Data architecture

| Store | Contents | Volume | Provenance |
|---|---|---|---|
| `data/registry/` | PM-JAY empanelled hospitals | 35,286 rows | **Real** |
| `data/reference/district_adequacy.csv` | Providers per district × specialty | 11,528 cells | **Real**, precomputed |
| `data/reference/access_distance.csv` | Km to nearest alternative district | 2,185 cells | **Real**, precomputed |
| `data/reference/specialty_canonical.csv` | 48 registry codes, two vintages → 24 canonical specialties | 24 | **Real**, derived |
| `data/reference/district_features.parquet` | Population, disease burden, infrastructure | 785 × 93 | **Real** |
| `data/external/ogd/` | 21 Ministry of Health tables tabled in Parliament (data.gov.in) | 21 tables | **Real** |
| `data/reference/state_context.csv`, `specialty_volume.csv` | Admissions, enforcement and network by state; admissions by specialty | 36 + 23 rows | **Real**, derived |
| `generate/corpus.py` (in memory) | Flagged-claim corpus: real placement, simulated conduct | 5,000 per run | **Simulated** |
| `results/` | Evaluation outputs | — | Produced from the simulated corpus |
| `out/decision_log.csv` | One row per case | — | Produced |
| `out/artefacts/` | Notices and escalation briefs | — | Produced |

Full field-level detail in [04-DATA-DICTIONARY](04-DATA-DICTIONARY.md).

---

## 7. Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Agent framework | **CrewAI 1.15.20** | Mandated for Path A. Verified to resolve on Python 3.12 and 3.13, 135 packages, no torch |
| Language | Python 3.12 | 3.13 also resolves; pin 3.12 for the team so everyone matches |
| Schemas | Pydantic v2 | Typed contracts at every boundary; invalid agent output is caught, not propagated |
| Data | pandas + pyarrow | Reference tables are small and columnar |
| Geometry | shapely | District representative points; already available |
| Env | uv | Fast, reproducible, trivially pins the interpreter |
| LLM | Gemini (`gemini-3.8-flash`, free tier) for development and the demo; Claude (`claude-opus-5`) for the final presentation — each through its official SDK behind one CrewAI adapter contract | Swapping models changes what agents conclude, never the rules that act on it; any model failure degrades to the deterministic rules path |
| Evidence viewer | SvelteKit + TypeScript, read-only, prerendered to static files (`frontend/`) | **Not required by Path A**, and it decides nothing: it reads the exported record of decisions already made. Built because a reviewer should be able to see what the crew did without reading a CSV. Carries a bundled map of India (DataMeet boundaries, MIT, Survey of India depiction) so the district a gate decision turns on can be seen rather than only read. No server, no key, no network at runtime |

---

## 8. Non-functional requirements

| ID | Requirement | Rationale |
|---|---|---|
| NFR-1 | The pipeline completes with no LLM key, marking decisions `degraded` | Demo must never die on an API failure |
| NFR-2 | Every decision is traceable to a rule and an evidence citation | A hospital has 5 days to answer and must know what it is answering |
| NFR-3 | Reference lookups are O(1) from precomputed tables | Adequacy is arithmetic; it must not cost an LLM call |
| NFR-4 | Runs are reproducible given a seed | The recorded demo must match what anyone can re-run |
| NFR-5 | No real hospital identity appears in any fraud scenario output | EC-1 |
| NFR-6 | Malformed input is refused with a reason, never guessed | A fabricated district silently corrupts the access gate |

---

## 9. Key design decisions

| ID | Decision | Alternatives rejected | Why |
|---|---|---|---|
| **DD-1** | Gate on the *conflict*, not on pivotality | Escalate all thin districts | Adequacy alone flags 30.7% of cells — escalating all of them makes this Path B in costume |
| **DD-2** | Precompute adequacy to a lookup table | Compute inside an agent | It is arithmetic. An agent wrapper would be decoration, and an obvious one |
| **DD-3** | Triggers are NHA's published rules, not a learned model | Train an anomaly detector | Training on invented fraud learns the generator. Also unlicensed — see EC-2 |
| **DD-4** | The district is the decision unit | Travel-time catchments | It is where the committee sits and the registry is keyed. Stated as a known limitation |
| **DD-5** | Capability produces a *flag*, not a *conclusion* | Assert incapability | Cannot be established without a site visit. Mirrors the real process |
| **DD-6** | Pseudonymous provider identity in scenarios | Use real hospital names | Fabricated accusations against identifiable institutions. EC-1 |
| **DD-7** | Build deterministically first, add agents last | Agents first | Makes `degraded` a real path and lets tools be tested with no LLM |

---

## 10. Known limitations

Declare all of these in the report. They are strengths when volunteered and weaknesses when found.

1. **Claims are simulated.** Detection is therefore circular by construction. We claim no detection
   novelty; the contribution is the action decision.
2. **The registry records entitlement, not capability.** Punjab and Gujarat bulk-empanel basic
   facilities — with Telangana they account for 95% of CHC/PHC tertiary listings. This means our
   access figures are **optimistic**; real coverage is thinner.
3. **Distances are straight-line** between district representative points. Road travel is roughly
   1.3–1.5× further, so the figures are conservative.
4. **Three states are excluded** — Delhi, Odisha, West Bengal never implemented PM-JAY.
5. **Post-2019 district reorganisation** is not fully reflected in the registry; newly created
   districts appear artificially thin.
6. **Patients cross district lines.** The district is a decision unit, not a catchment.
7. **The rules desk does not check a beneficiary's own admissions for copied paperwork.**
   `ClaimStore.reused` reports a document byte-identical to another claim's only when the
   beneficiary differs (the T6 question). On T7 — one beneficiary's repeat admissions — copied
   paperwork is the fraud signature, and `ClaimStore.identical` would separate the benchmark's
   T7 cases exactly (all three frauds carry byte-identical notes, none of the three innocent
   cases do), but no deterministic check reports it: only an agent comparing the admissions
   does. The crew's advantage on cross-claim cases is therefore partly an artefact of a gap in
   the rules, not only of reading ability. Left open deliberately (team decision, 18 September),
   and stated here rather than discovered: closing it is a change to what the rules can do, and
   would be measured before it was claimed.
