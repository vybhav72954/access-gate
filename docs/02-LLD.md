# 02 · Low-Level Design

**Project:** The Access Gate
**Version:** 1.0
**Audience:** Crew engineer, policy owner
**Prerequisite:** [01-HLD](01-HLD.md)

---

## 0. As built — 18 September 2026

The pipeline is implemented and **318 tests pass** (`pytest`, ~6 min, no API key needed). Where the build
departed from the design below, the code is authoritative and the reason is recorded here.

| # | Design said | As built | Why |
|---|---|---|---|
| B-1 | Specialties are raw registry codes (`S12`) | **24 canonical specialties** (`S12` and `MC` → `cardiology`) | Hospitals list one code vintage or the other; raw codes split providers and overstated every adequacy figure (data defect D-6) |
| B-2 | Different triggers route to different channels | **Desk audit always runs first; field channels are ordered only when the desk is inconclusive**, and consumed when their reports are on file | The guidebook gives every trigger a checklist in all four channels. Real branching is evidence-driven escalation, which is also how SAFU works |
| B-3 | Agents call tools | **The orchestrator invokes the deterministic tools and places results in each agent's case file** | Removes tool-loop fragility for the demo; agents still reason over tool results. **Superseded by B-32:** it left the crew without work |
| B-4 | Investigators see everything | **Investigators are blind to network adequacy** | Whether a hospital committed fraud must not depend on who would lose access. The gate applies afterwards, in `policy.py` |
| B-5 | R1 is severity 3 | **R1 is severity 2** (show-cause, not suspension) | CAG 11/2023 §4.5: hospitals deliver specialties they are capable of but never applied for |
| B-6 | Rare specialty skips the gate | **Unknown adequacy escalates to a human** (`ADEQUACY_UNKNOWN`) | An irreversible action with an unknowable access consequence is a human's call |
| B-7 | Gate = sole provider | **Protect if sole provider ≥ 50 km from an alternative, or ≤ 2 providers in an aspirational district**; every applicable reason recorded | Makes the distance figure load-bearing; `DISTANCE_MATERIAL_KM` is the number to sweep |
| B-8 | Low confidence → no action | **Low confidence buys evidence first** (field audit when the trigger has field channels and none are on file); a human only after that | At a 0.18% prior, guessing is worse than asking |
| B-9 | CrewAI's built-in Anthropic provider | **`crew/llm.py`: a CrewAI `BaseLLM` on the official Anthropic SDK** | CrewAI 1.15.20's provider forces `tool_choice` with one tool (conflicts with Opus 5's default thinking) and uses the deprecated `output_format` beta |
| B-10 | Service after death: a date in the death register is proof (0.95) | **Proof only with a death certificate on file; a register entry alone orders field verification** | Guidebook trigger 10 verifies a date-of-death mismatch with the relatives and the certificate. The evaluation found every register error became a suspension (09-EVALUATION §6, E-1) |
| B-11 | Each trigger names its field channels | **Every egregious trigger orders two independent field channels** (T6 and T10 gained `beneficiary_call`) | One wrong field report above the confidence floor could suspend an innocent hospital (E-2). Locked by `test_no_single_field_report_can_suspend_a_hospital` |
| B-12 | A claim corpus file in `data/synthetic/` | **`generate/corpus.py` generates flagged claims in memory; `metrics/run.py` measures them** | Nothing simulated is written next to real data. Placement is real and calibrated; conduct is simulated with named, swept assumptions |
| B-13 | One LLM, Claude | **Two providers behind one contract: Gemini (demo) and Claude (final presentation)**, each on its official SDK | Gemini has a free tier. CrewAI 1.15.20's built-in Gemini provider strips `null` from output schemas, so an agent could never answer *inconclusive* — the answer that orders a field audit. Our adapter keeps it; `test_an_inconclusive_desk_reading_orders_a_field_audit` proves it end to end |
| B-14 | Agents' readings are advisory | **Recorded facts outrank agent readings, deterministically**: the registry lookup's stance wins in both directions; a field team's recorded stance always stands and an agent may only discount it; drafted prose must pass `draft_problems()` or the template ships instead | Live runs showed an agent turning "registry consistent" into "clears the claim" at 1.00, and drafts overstating access loss and actions. Instructions reduce these; only code rules them out (F-08, F-09) |
| B-15 | The desk clears on an explanation | **The desk may clear a claim only when the package's mandatory document categories are on file**; T4 justification is read from clinical documents only | NHA's trigger-2 checklist: "Verify mandatory documents for blocked procedure" (F-06). A package name in a discharge summary cleared padded stays (F-10) |
| B-16 | Intake checks presence of fields | **Intake also refuses impossible timelines** (discharge before admission, submission before discharge) | A negative stay clamped to zero and fired zero-length-of-stay triggers (F-17) |
| B-17 | The registry is evidence | **The registry is evidence for R1 only, and a blank specialty list is missing data**: R1 needs a non-empty list; the gate returns `unknown` (→ escalate) for such hospitals | D-4: 30% of hospitals list nothing (F-12, F-13) |
| B-18 | Adequacy needs the district's geometry | **Provider counts need no geometry**: districts without a boundary get adequacy with no distance; a hospital not empanelled for the billed specialty is not protected by the gate | A crash on 167 listed hospitals (F-11); a false "sole provider" (F-15) |
| B-19 | `decide(..., has_field_reports: bool)` | **`decide(..., field_on_file: frozenset[Channel])`**: an egregious finding resting on field evidence waits for every channel the trigger orders | One report could still suspend (F-14); E-2 now holds in the policy itself |
| B-20 | The case file shows the guidebook checklist | **Desk-audit checks (transcribed from the guidebook's desk column) are shown apart from field checks, which are labelled as not prerequisites** | The flattened PDF mixed them; a live agent refused to settle S2 on a death certificate until the family was asked (F-21) |
| B-21 | The gate checks the specialty billed | **The gate checks every specialty the hospital is empanelled for**: `decide(..., network=...)` (required, no default) escalates when any of them would lose its only real provider, and names each one (`specialties_at_stake`). Only the billed specialty decides a de-listing referral | Suspension removes a hospital from all its specialties. Gating on the billed one auto-suspended the only real provider of something else in 6.5% of confirmed egregious findings (F-22) |
| B-22 | The desk matches keywords | **The desk reads negation** (a cue within five words before a mention in the same clause, or an absence stated right after it) | "No complications", "non-critical course" and "Not a LAMA case" cleared claims (F-23) |
| B-23 | T10: admission timestamp after death timestamp | **Calendar dates** | A register records a date; a same-day in-hospital death read as billing after death (F-24) |
| B-24 | T7: 3 acute admissions within 30 days either side | **The 30 days up to and including this admission** | Either side spanned 60 days and counted admissions not yet made (F-30) |
| B-25 | The registry lookup's stance wins | **Its stance and its confidence win** | An agent could keep the stance and change the weight of a deterministic fact (F-26) |
| B-26 | Package rates and day-care list prepared by hand | **`scripts/build_packages.py`** rebuilds the rates byte for byte and the day-care list with a word-bounded name rule (119 → 95) | The substring rule put 17 major burns surgeries and COPD on the day-care list, blinding T2 and T3 (F-33) |
| B-27 | District names resolve against the boundary file | **Against every district in the network** | Hospitals in the 23 districts without geometry could not be named (F-25) |
| B-28 | A desk finding at or above the floor carries an egregious trigger | **Only a trigger whose desk evidence is proof in itself may skip the field (`desk_can_suspend`): T10, with a certificate dating death before admission.** T6 (reused document) withholds the claim and waits for a hospital visit and a beneficiary call | Team decision, 17 September: at the desk, reuse and an honest upload error look the same; the live crew asked for the same on S5 (F-07) |
| B-29 | A package has one specialty | **A package has every specialty it is listed under** (`hbp_package_listings.csv`); its own is the one its code names; R1 needs none of them to be empanelled; R3 needs every billable listing reserved | The first-sorted listing exposed 15,419 hospitals to false R1 notices and misread 52 reserved flags (F-35) |
| B-30 | Claims are validated where they are built | **`process()` re-validates every claim; unknown fields are errors; ids are plain tokens; timestamps are Indian time; identifiers are normalised; hashes are recomputed** | `model_copy()` skips validation: a claim id wrote outside the output directory, and mixed timestamps crashed intake (F-36 to F-38) |
| B-32 | Agents call tools (B-3 deferred it "after live testing") | **The agents call their own tools**, through CrewAI's native function calling in both adapters. Claude's thinking blocks and Gemini's thought signatures are kept across tool turns, as each API requires | Under B-3 no agent had a tool, two of the four agents changed no decision, and no evaluation ran the crew: the framework was decoration (risk R-02 realised, F-54). The guidebook's desk checks for T6 and T7 compare documents across claims, and comparing needs fetching |
| B-33 | Four agents: triage, desk audit, registry verification, adjudicator | **Two agents: the Desk Investigator** (five evidence tools) **and the Enforcement Officer** (the decision, the access impact, seven action tools). Registry verification is a deterministic finding | The triage plan was never read; the registry agent was always overridden by the lookup (B-14, B-25); the adjudicator's drafting moved to the officer, who now also acts |
| B-34 | The policy decides after the crew | **The policy decides inside the crew**, in the review task's callback: the evidence rules (`crew/guardrails.py`), then `policy.decide()`. The agents that act start from that decision | Keeps "agents propose, the policy disposes" inside one crew with a hand-off |
| B-35 | A draft that fails a check is silently replaced by the template | **The officer acts through gated action tools.** Each executes only the policy's decision, and only with text that passes the publication checks. A rejected draft goes back to the officer with its reasons (at most three times). A decision the officer does not execute is executed by the orchestrator with the template (`acted_by`) | Acting is the agent's work; deciding stays the policy's. The checks became feedback the agent acts on |
| B-36 | An agent reads one claim | **Tools read only this claim and claims a tool has shown to be related to it. A stance on T5, T6 or T7 needs those claims examined** (a CrewAI task guardrail: one retry, then the case degrades) | The guidebook's desk checks. An agent may not conclude on repeat admissions without looking at them, nor browse the corpus |
| B-37 | Artefacts show findings | **Every artefact and log row shows the investigation trail**: each tool call, with refused calls marked. A rejected draft appears as its length and the problems found, never its words | Agent work must be auditable, and a rejected overstatement must not reach the artefact through its trail |
| B-38 | The desk checks three mandatory document categories | **Every category the package master names**: discharge summary, pre-procedure evidence, operative notes, histopathology, photographs, post-procedure imaging, investigation reports, indoor case papers (`required_documents()`). The corpus files the same categories, and the evaluation is byte-identical | The live crew found a scenario missing its package's histopathology and photograph (F-55), and cleared admissions whose investigation reports were never filed (F-56) |
| B-39 | Rules and crew are measured on neutral documents only | **An agent benchmark**: 40 hand-written cases whose truth sits in free text or across claims, with both paths scored (`generate/benchmark.py`, [10-AGENT-EVALUATION](10-AGENT-EVALUATION.md)) | The 5,000-case evaluation cannot tell whether an agent reads documents better than the patterns do; the team asked for evidence either way |
| B-40 | A clearance needs the package's mandatory documents | **...and the document that would explain the trigger: an invoice or bill for R2, a referral for R3**, on both paths | The crew cleared both without them on the benchmark, reading the gap as an upload slip (F-58) |
| B-41 | The Gemini adapter retries a 429 on the server's delay | **A 429 naming a per-day quota marks the model spent and fails at once; the primary is tried again when the backup runs out** | A 53-second retryDelay accompanies a spent daily quota; the adapter waited four minutes a call (F-57) |
| B-42 | Two agents: the Desk Investigator and the Enforcement Officer (B-33) | **Six agents**: Desk Investigator (the documents' integrity), **Medical Auditor** (clinical need, on T2, T3, T4, T7), **Field Evidence Analyst** (the weight of each field report), **Audit Reviewer** (does each reading hold), **Committee Liaison** (the Committee's brief, on a referral), Enforcement Officer (executes and explains) | Team decision, 17 September: two agents answered one question between them. A desk reading, a clinical reading and the weight of a field report are different questions, asked of different evidence by different people in NHA's own process; splitting them makes each finding attributable and reviewable |
| B-43 | One reading of the file, by one agent | **Readings are independent**: the four readers' tasks take no context, and no reader sees another's finding. The Medical Auditor is not shown the desk's conclusion, neither is shown the field reports, and the Billing & Tariff Analyst reads the money without any of them. The advocate and the reviewer do receive them, one to answer them and one to check them | Two readings that saw each other are one reading with a witness. Independence is what makes a conflict between them evidence (and a conflict falls below the confidence floor, which buys field evidence) |
| B-44 | A reviewer that can confirm or overturn a finding | **Dispute only**: the Audit Reviewer may set aside any finding on the case - the desk's, the medical, the billing reading or the advocacy; a disputed reading weighs nothing (stance `null`, confidence 0). What the case does next depends on the readings left standing: where none is left, or those left fall below the confidence floor, it buys field evidence or goes to a human. It cannot convict, clear, or re-weigh, and it may not dispute a field report or the registry lookup | Team decision: a reviewer that could confirm would be a second vote on the same evidence, and one that could convict would be an agent deciding an action. Doubt is what a review can honestly add. **The earlier wording of this row promised that a dispute always bought field evidence or a human; it does not, and F-61 is the case that proved it** |
| B-44a | A dispute may set aside any reading it can name | **A dispute may set aside a reading, never a measurement**: where `ClaimStore.identical` itself finds a document on the claim byte-identical to another claim's, a dispute of a reading that supports fraud is **refused** and recorded (`disputes_refused`); the reading keeps its weight. A dispute of any other reading on the same case still applies in full | F-61. Removing a reading takes it out of the confidence denominator as well, so striking the only dissent turns a split verdict into apparent unanimity and *raises* the survivor's confidence — on BCH-T7-03, from 0.46 to 0.85, which released a fraud. A reviewer that cannot clear a case must not be able to clear it by subtraction. Refusing leaves exactly the decision the case would have reached had the reviewer said nothing |
| B-47 | Six agents, the roster fixed by the trigger | **Nine agents**: a **Case Router** (which optional work to open), a **Billing & Tariff Analyst** (is the amount accounted for) and a **Provider Advocate** (the hospital's side) join the six. Team decision, 18 September | The six left two questions unowned and one unasked. Nobody owned the money — BCH-R2-06, the one case *both* paths got wrong, is arithmetic: ₹12,000 of implant against a ₹60,000 excess, read as justifying it. And nobody put the hospital's side before it was acted against, which is the part of NHA's process an automated desk most easily drops |
| B-48 | The router plans the case, or the trigger fixes the roster | **The router may only widen**: the rules compute a mandatory set of tracks from the trigger and the file (`mandatory_tracks`); the Case Router's plan can add tracks to it and can never remove one. It is given **no evidence tool**, and it may not open a track there is no evidence for (the field track with no report on file) | F-54 was a triage agent whose plan was never used, so the plan had to become the control flow this time. But a model deciding what *not* to investigate is a model deciding the case: the floor keeps the rules' coverage whatever the router thinks, and the ceiling is only ever more work. Giving it no tool keeps it a router: one that has read the documents has already formed the reading its readers are meant to reach independently |
| B-49 | The advocate weighs against fraud, like a reader | **The defence can slow a case, never clear one**: the Provider Advocate returns an explanation and whether the evidence on file `excluded` it. An unexcluded explanation turns SHOW_CAUSE or SUSPEND into a field audit or a human review (`policy.defended`, reason code `DEFENCE_UNEXCLUDED`); it can never produce a release, and an escalation or de-listing referral already reaches the Committee, so those stand. The Audit Reviewer may dispute the advocacy, and a disputed defence never reaches the policy | An advocate that added a fraud-opposing finding would be a thumb on the scale, and on a benchmark of 20 frauds it would argue them free. What due process actually requires is narrower: not that the hospital be believed, but that no irreversible step be taken while an innocent account the file supports stands unexamined. Asymmetry is what makes it safe to give an agent a voice for one side |
| B-50 | A guardrail checks an answer's shape; its substance is the agent's business | **Five checks on substance** (F-63): a stance must cite something; a stance on the money must have called `claim_tariff`; a defence that would stop an action must have read the file; the reviewer's summary may not assert a dispute its list omits; and the router's reasons are recorded on the Decision, not only its tracks | Each was something a live run did. An agent can produce a well-formed answer that no evidence supports - a finding at 0.93 citing nothing, an excess never computed, a summary disputing what the list leaves standing - and every one of those passes a schema. The guardrail is where "the answer must be earned" belongs, because the policy downstream cannot tell an earned finding from an unearned one |
| B-45 | The Committee's brief is the artefact's template question | **The Committee Liaison files the brief** (`file_committee_brief`): the evidence and the access consequence side by side, two to four options genuinely open to the Committee with their consequences, a recommendation and the one question. It passes the same publication checks as a notice, and a rejected brief goes back with its reasons (at most three). Without a filed brief the artefact keeps the standard question | An escalation is a request for a decision, and a request without options is not one. The checks keep the brief from asserting a suspension or a de-listing the Committee has not ordered |
| B-46 | The Decision records the trail | **...and which agents worked the case (`agents`), what the router opened and why (`opened_by_router`, `router_reasons`), which findings were disputed (`disputed`) and which disputes were refused (`disputes_refused`), the hospital's side (`defence`, `defence_excluded`), the reviewer's summary (`review`) and the Committee brief.** A degraded case records none of them: the rules decided it | Nine agents make "who worked this case" a fact worth auditing, and a degraded decision must not look like the crew's |
| B-31 | A referral clears a reserved package | **Only a package the master allows on referral (`referral_basis`: 9 of 189 reserved listings)** | A letter cleared private billing of strictly reserved packages (F-51) |

**Claude configuration** (`crew/llm.py`): `claude-opus-5`; adaptive thinking by omission; no `temperature`
or `top_p` (rejected on Opus 5); structured output via `client.beta.messages.parse(output_format=...)`;
**server-side refusal fallbacks on** (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`).
Refusals and truncation raise, and the runner degrades to rules.

**Gemini configuration** (`crew/gemini.py`, for development and the demo): `gemini-3.8-flash` (free tier),
backup `gemini-3.5-flash-lite`, both overridable in `.env`. Structured output via `response_json_schema` built from
the Pydantic model **with nulls preserved**, validated by Pydantic. A safety block retries once on the backup model
(client-side; Gemini has no server-side fallback). An overloaded or out-of-quota primary gets one quick retry, then the backup serves the rest of the run — seen live on 16 September, when `gemini-3.8-flash` returned 503 "high demand" and the backup answered. Calls are paced to `GEMINI_MAX_RPM` (10); 408/429/5xx are
retried with backoff on the server's `retryDelay`, and a wait over 90 s (spent daily quota) degrades at once.
Safety thresholds are set to `BLOCK_ONLY_HIGH`, because case files discuss death and fraud.
`tests/test_gemini_contract.py` mirrors the Claude contract tests offline.

**Choosing:** `--provider gemini|claude`, default `LLM_PROVIDER` in `.env`. The model a decision ran on is
recorded in the decision log (`model`) and in each artefact's Mode line.

**Offline proof the crew works:** `tests/test_gemini_contract.py` and `tests/test_llm_contract.py` drive the full
crew through each adapter. A scripted model (`tests/fakes.py`) plays all nine agents through CrewAI's own tool loop, so
tool calls, refusals, the guardrail retries, the router's plan, the hand-off to the advocate and the reviewer, its
disputes and refused disputes, the policy between the agents, the liaison's brief and the officer's gated actions all
run as they do live. `tests/test_agent_tools.py` calls the tools directly. **Live proof:** the ten scenarios on Gemini
(05-TEST-PLAN §5, F-54 to F-56) and the agent benchmark ([10-AGENT-EVALUATION](10-AGENT-EVALUATION.md)).

### Module map, as built

```
crew/schemas.py      Pydantic contracts; EC-1 enforced by the Claim type
crew/tools.py        deterministic lookups over data/reference
crew/investigate.py  the rules desk (the degraded path) and the evidentiary preconditions both desks meet
crew/crew_llm.py     the CrewAI crew (@CrewBase): route -> investigate, medical audit, field review, billing audit
                     -> advocacy -> review -> policy -> committee brief -> enforce; each agent's case file
crew/config/         agents.yaml and tasks.yaml: the crew's nine agents and nine tasks
crew/agent_tools.py  the agents' tools, bound to one case: five evidence tools and the tariff; decision, access, the
                     Committee brief and seven gated action tools; the investigation trail
crew/guardrails.py   deterministic checks on agent output: evidence rules and publication rules
crew/llm.py          Claude via the Anthropic SDK, as a CrewAI LLM (final presentation)
crew/gemini.py       Gemini via the google-genai SDK, as a CrewAI LLM (development, demo)
crew/providers.py    --provider / LLM_PROVIDER -> the adapter
crew/actions.py      the only side effects: artefacts, field-audit queue, decision log
crew/run.py          orchestration + CLI
rules/triggers.py    T2-T10 (NHA) and R1-R3 (derived); ClaimStore for cross-claim triggers
rules/policy.py      constants with reasons; weigh(), access_gate(), decide()
generate/fixtures.py the ten scenarios, selected from real data and asserted
generate/corpus.py   a year of flagged claims: real placement, simulated conduct
generate/benchmark.py  the agent benchmark: 40 hand-written cases whose truth is in the documents
metrics/run.py       auto-resolution, threshold sweep, disparate impact, ablation, sensitivity
metrics/benchmark.py rules versus the crew on the benchmark -> results/agent_benchmark.*, docs/10
scripts/build_reference.py   reference tables + figures.json, self-verifying
scripts/fetch_ogd.py         21 official tables from data.gov.in
scripts/build_state_context.py  official tables -> state_context, specialty_volume; reconciled
```

---

## 1. Module map (original design)

> This is the layout as first planned, kept for the record. It is **not** the repository as built: `crew/agents.py` and `crew/tasks.py` were never written, the agents and tasks live in `crew/crew_llm.py` with their canonical definitions in `crew/config/*.yaml`, and the crew is nine agents, not four. §2.4 of the handover notes carries the current map.

```
access-gate/
├─ data/
│  ├─ registry/PMJAY_empanelled_hospitals_2026-07-16.xls   # real export, 35,286 rows
│  ├─ reference/
│  │  ├─ district_adequacy.csv              # 11,528 cells
│  │  ├─ access_distance.csv                # 2,185 cells
│  │  ├─ district_features.parquet          # 785 × 93
│  │  ├─ hbp_package_rates.csv              # 1,602 pkgs: rate + required docs
│  │  ├─ daycare_candidates.csv             # 95, needs clinical review
│  │  └─ specialty_legend.json              # 89 codes, authoritative
│  └─ synthetic/claims.jsonl
├─ rules/
│  ├─ __init__.py
│  ├─ policy.py            # constants + decide()      <-- WRITE FIRST
│  └─ triggers.py          # NHA trigger evaluation + channel routing
├─ crew/
│  ├─ schemas.py           # Pydantic v2 contracts
│  ├─ tools.py             # five deterministic tools
│  ├─ agents.py            # four agents
│  ├─ tasks.py             # task definitions
│  ├─ actions.py           # side effects: notices, logs, queue writes
│  └─ run.py               # orchestration entry point
├─ generate/claims.py
├─ tests/test_scenarios.py
└─ out/{decision_log.csv, artefacts/}
```

---

## 2. Contracts (`crew/schemas.py`)

Pydantic v2. Every boundary is typed; invalid agent output is caught here, not propagated.

```python
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

class Channel(str, Enum):
    DESK_AUDIT        = "desk_audit"
    HOSPITAL_VISIT    = "hospital_visit"
    BENEFICIARY_CALL  = "beneficiary_call"
    BENEFICIARY_VISIT = "beneficiary_visit"

class Severity(int, Enum):
    ADMINISTRATIVE = 1   # documentation gaps
    SUBSTANTIVE    = 2   # clinical implausibility
    EGREGIOUS      = 3   # ghost patient, post-death, document reuse

class Action(str, Enum):
    RELEASE_CLAIM   = "release_claim"
    WITHHOLD_CLAIM  = "withhold_claim"
    SHOW_CAUSE      = "show_cause_notice"
    FIELD_AUDIT     = "order_field_audit"
    SUSPEND         = "suspend_hospital"
    DELIST_SPECIALTY= "delist_specialty"
    ESCALATE_SEC    = "escalate_to_sec"
    NO_ACTION       = "no_action_logged"
    REFUSE          = "refuse_malformed"

class Claim(BaseModel):
    claim_id:        str
    hospital_ref:    str                      # PSEUDONYMOUS — EC-1
    district_code:   int                      # real LGD code
    package_code:    str                      # real HBP 2022 code
    specialty_code:  str                      # real registry code, e.g. "S12"
    beneficiary_ref: str
    admission_ts:    datetime
    discharge_ts:    datetime | None
    los_days:        int = Field(ge=0)
    surgeon_ref:     str | None = None
    surgeon_reg_no:  str | None = None
    amount_claimed:  int = Field(ge=0)
    document_hashes: list[str] = []
    icu_flag:        bool = False
    prior_claims_30d:int = 0
    submitted_ts:    datetime

    @field_validator("hospital_ref")
    @classmethod
    def must_be_pseudonymous(cls, v: str) -> str:
        if not v.startswith("HOSP-"):
            raise ValueError("EC-1: provider identity must be pseudonymous")
        return v

class TriggerHit(BaseModel):
    trigger_id:  int                          # NHA annexure number
    name:        str
    severity:    Severity
    channels:    list[Channel]                # THE BRANCH
    checklist:   list[str]                    # from the guidebook

class AgentFinding(BaseModel):
    agent:      str
    channel:    Channel | None
    conclusion: str
    supports_fraud: bool
    confidence: float = Field(ge=0.0, le=1.0)
    citation:   str                           # NFR-2: rule or document reference

class Adequacy(BaseModel):
    district_code:      int
    specialty_code:     str
    n_providers:        int
    km_to_alternative:  float | None
    population:         int
    aspirational:       bool
    capability_ok:      bool                  # flag, NOT a conclusion — EC-3
    state_convention:   bool                  # Punjab/Gujarat bulk-empanelment

class Decision(BaseModel):
    case_id:        str
    claim_id:       str
    trigger_id:     int
    channels_used:  list[Channel]
    findings:       list[AgentFinding]
    conflicts:      list[str]
    adequacy:       Adequacy | None
    action:         Action
    reason_codes:   list[str]
    confidence:     float
    degraded:       bool = False
    human_required: bool = False
    artefact_path:  str | None = None
    decided_ts:     datetime
```

---

## 3. Policy (`rules/policy.py`)

**Write this file first.** Every constant carries a written reason. This file is what a reviewer will
attack, and its owner must defend each line aloud.

```python
# ── thresholds ─────────────────────────────────────────────────────────────
CONFIDENCE_FLOOR = 0.70
# Below this the system acts on nothing but still logs. The real process
# already tolerates a queue, and a wrong show-cause notice costs a hospital
# five days of response time it should not have had to spend.

SOLE_PROVIDER = 1
# Exactly one real provider for the specialty in the district. Not a
# judgement — this is the count at which enforcement zeroes out a capability.

THIN_NETWORK = 2
# <= 2 providers triggers the adequacy check. 30.7% of cells sit here, which
# is precisely why THIN_NETWORK alone must NOT escalate (see DD-1).

WELL_SERVED = 10
# >= 10 providers: adequacy is not a factor at all. 33.3% of cells qualify.

DISTANCE_MATERIAL_KM = 50.0
# Median distance to an alternative is 51 km; 50.7% of sole-provider cells
# exceed 50 km. Sweep this and publish the curve — do not assert it.

ASPIRATIONAL_SHIFT = 1
# NITI aspirational districts move one band stricter. Their sole-provider
# rate is 27.0% against 17.9% elsewhere, and NHA's own guidelines annexe
# the ADP list, so the carve-out is already policy.

SEVERITY_SUSPEND = Severity.EGREGIOUS
# Only band 3 reaches suspension. Post-death service, ghost patients and
# document reuse are band 3; extended-stay anomalies are band 1.

RARE_SPECIALTY_MIN_DISTRICTS = 50
# Six specialties exist in fewer than 50 districts nationally (two in a
# single district each). Excluded from adequacy scoring — no district can
# reasonably be expected to hold them.
```

### 3.1 The decision function

```python
def decide(claim, hit, findings, adequacy, degraded=False) -> Decision:
    """Pure function. No I/O, no LLM. Fully unit-testable."""

    if claim is None or malformed(claim):
        return _d(Action.REFUSE, ["MALFORMED_INPUT"], human=False)

    conf = aggregate_confidence(findings)
    fraud = any(f.supports_fraud for f in findings)

    # 1. cleared on the evidence
    if not fraud:
        return _d(Action.RELEASE_CLAIM, ["TRIGGER_CLEARED"])

    # 2. below the floor — never act, never go silent
    if conf < CONFIDENCE_FLOOR:
        return _d(Action.NO_ACTION, ["BELOW_CONFIDENCE_FLOOR"], human=True)

    # 3. not yet serious enough to touch the network
    if hit.severity < SEVERITY_SUSPEND:
        if needs_physical_evidence(hit):
            return _d(Action.FIELD_AUDIT, ["NEEDS_FIELD_EVIDENCE"])
        return _d(Action.SHOW_CAUSE, ["CONFIRMED_DOCUMENTARY"])

    # ── 4. THE ACCESS GATE ────────────────────────────────────────────────
    band = adequacy_band(adequacy)          # applies ASPIRATIONAL_SHIFT

    if band == "well_served":
        return _d(Action.SUSPEND, ["SERIOUS", "ALTERNATIVES_EXIST"])

    if adequacy.n_providers <= SOLE_PROVIDER:
        if not adequacy.capability_ok:
            # phantom capability — the opposite action
            return _d(Action.DELIST_SPECIALTY,
                      ["SOLE_PROVIDER", "CAPABILITY_IMPLAUSIBLE",
                       "DISTRICT_UNCOVERED"], human=True)
        return _d(Action.ESCALATE_SEC,
                  ["SERIOUS", "SOLE_REAL_PROVIDER",
                   f"KM_TO_ALTERNATIVE={adequacy.km_to_alternative:.0f}",
                   f"POPULATION={adequacy.population}"], human=True)

    return _d(Action.SUSPEND, ["SERIOUS", "THIN_BUT_NOT_SOLE"])
```

**Note the shape.** Steps 1–3 resolve the majority. The gate at step 4 fires only on
`severity == EGREGIOUS` **and** `n_providers <= 1` — the conflict, not mere thinness. This is DD-1,
and it is what keeps the auto-resolution share high enough for Path A to be honest.

---

## 4. Triggers (`rules/triggers.py`)

Deterministic. Each maps to an NHA annexure entry, and each declares its evidence channels — the
routing table that makes the crew branch.

| ID | Name | Detection | Severity | Channels |
|---|---|---|---|---|
| 2 | Zero LOS, major surgical | `los_days == 0 and is_major_surgical(package)` | 2 | hospital_visit, desk_audit |
| 3 | Zero LOS, non-day-care medical | `los_days == 0 and specialty in {M1, M2}` | 2 | desk_audit |
| 4 | Extended medical management | `los_days > 10 and not icu_flag` | 1 | desk_audit |
| 5 | Impossible surgeon | same `surgeon_reg_no`, same date, districts > 200 km apart | 3 | hospital_visit, beneficiary_call |
| 6 | Document reuse | identical hash across ≥2 claims, different beneficiaries | 3 | desk_audit |
| 7 | Repeat acute episodes | ≥3 acute claims, one beneficiary, 30 days | 2 | beneficiary_call |
| 10 | Post-death service | `admission_ts > beneficiary.death_date` | 3 | beneficiary_visit, desk_audit |
| R1 | Specialty not empanelled | `specialty_code ∉ registry_lookup(h).specialties` | 3 | desk_audit |
| R2 | Amount exceeds published rate | `amount_claimed > hbp_lookup(pkg).amount_rs * 1.10` | 2 | desk_audit |
| R3 | Govt-reserved package billed by a private hospital | `govt_reserved and type != Public` | 2 | desk_audit |

**R1, R2 and R3 are checked entirely against real published data** — the registry's specialty list,
the published package rate, and the government-reserved flag. They need no simulation at all. Call
them out in the report: they are the strongest single answer to *"your claims are synthetic"*, because
three of the detections run against genuine government-published facts.

---

## 5. Tools (`crew/tools.py`)

Deterministic, side-effect free, O(1) from precomputed tables (NFR-3). Wrapped as CrewAI tools.

```python
@tool("trigger_guidance")
def trigger_guidance(trigger_id: int) -> TriggerHit:
    """NHA guidebook Annexure 2: checklist, evidence channels, severity band."""

@tool("registry_lookup")
def registry_lookup(hospital_ref: str) -> dict:
    """Real registry record: type, district_code, state, specialties[], contact.
    Resolves the pseudonym to its real district and type — never to a name."""

@tool("district_adequacy")
def district_adequacy(district_code: int, specialty_code: str) -> Adequacy:
    """Providers in district, km to nearest alternative district with the
    specialty, population, aspirational flag, capability plausibility."""

@tool("hbp_lookup")
def hbp_lookup(package_code: str) -> dict:
    """{specialty_code, specialty_name, amount_rs, pre_investigations,
        post_investigations, govt_reserved, is_major, is_daycare_candidate}

    Backed by data/reference/hbp_package_rates.csv (1,602 packages, Punjab SHA).
    pre/post_investigations are the DOCUMENTS THE DESK AUDIT MUST FIND -- this is
    what NHA's trigger guidance means by "verify mandatory documents for blocked
    procedure". is_major = amount >= 20,000 and not a day-care candidate, which
    is what makes trigger 2 well-defined."""

@tool("capability_flag")
def capability_flag(hospital_ref: str, specialty_code: str) -> dict:
    """{plausible: bool, reason: str, state_convention: bool}
    EC-3: returns a FLAG, never a conclusion. state_convention=True for the
    Punjab/Gujarat bulk-empanelment pattern, which must not be read as fraud."""
```

### 5.1 `capability_flag` logic

```python
BASIC_TIER   = {"CHC", "PHC", "COMMUNITY HEALTH", "PRIMARY HEALTH"}
TERTIARY     = {"S12", "S9", "S8", "M6", "S13", "OT"}
BULK_STATES  = {"PUNJAB", "GUJARAT", "TELANGANA"}   # 96% of such listings

def capability_flag(hospital_ref, specialty_code):
    h = registry_lookup(hospital_ref)
    basic = any(t in h["name_tier_hint"] for t in BASIC_TIER)
    if not (basic and specialty_code in TERTIARY):
        return {"plausible": True, "reason": "no tier/specialty mismatch",
                "state_convention": False}
    if h["state"].upper() in BULK_STATES:
        return {"plausible": True,          # NOT a fraud signal
                "reason": "state bulk-empanelment convention",
                "state_convention": True}
    return {"plausible": False,
            "reason": f"{h['type']} listed for tertiary {specialty_code}",
            "state_convention": False}
```

The `BULK_STATES` branch is essential. Without it the flag fires on Punjab and Gujarat's data
convention rather than on misconduct — a false-positive generator, and a finding that would have
embarrassed the team. See [06-RISK-REGISTER R-04](06-RISK-REGISTER.md).

---

## 6. Agents (`crew/agents.py`) — original design, superseded by §6a

```python
triage = Agent(
    role="Investigation Triage Officer",
    goal="Given a fired trigger, determine which evidence channels must be "
         "pursued and what facts must be established in each.",
    backstory="You work in a State Anti-Fraud Unit and follow NHA's "
              "Anti-Fraud Framework Practitioners' Guidebook exactly. "
              "Different triggers demand different verification paths.",
    tools=[trigger_guidance], allow_delegation=False)

desk_audit = Agent(
    role="Desk Medical Auditor",
    goal="Examine the submitted claim documents against the trigger's "
         "checklist and state whether they support the billed episode.",
    backstory="You audit clinical documentation. You cite the specific "
              "document and field behind every conclusion.",
    tools=[hbp_lookup], allow_delegation=False)

registry_verification = Agent(
    role="Registry Verification Officer",
    goal="Test every claimed fact against the published empanelment "
         "registry. Report contradictions plainly.",
    backstory="You check what a hospital claims against what the national "
              "registry records. You never assert capability you cannot "
              "verify — you raise a flag for field verification.",
    tools=[registry_lookup, capability_flag], allow_delegation=False)

adjudicator = Agent(
    role="Enforcement Adjudicator",
    goal="Reconcile conflicting findings, apply the decision policy, and "
         "draft either a show-cause notice or an escalation brief.",
    backstory="You decide what the State Health Agency does next. You know "
              "that removing a hospital from the network can remove a "
              "district's only source of a specialty, and that this "
              "judgement is reserved for the State Empanelment Committee.",
    tools=[district_adequacy], allow_delegation=False)
```

**Prompt discipline:** the adjudicator must never invent an action. It proposes; `rules.policy.decide()`
disposes. The LLM drafts prose; the decision itself is a pure function.

## 6a. The crew as built (supersedes §6 and §7's crew calls)

One CrewAI crew, `AccessGateCrew` (`crew/crew_llm.py`, `@CrewBase`), with its agents and tasks in
`crew/config/agents.yaml` and `crew/config/tasks.yaml`. It runs once per flagged case that reaches the desk,
sequentially. Nine tasks; five are `ConditionalTask`s, which run only when the case calls for them.

```
route            (Case Router)            every case, first — its plan may only widen what follows
                                              │
                                              ▼
investigate      (Desk Investigator)      every case          ─┐
medical_audit    (Medical Auditor)        clinical trigger     │  four independent readings
field_review     (Field Evidence Analyst) reports on file      │  of one file
billing_audit    (Billing & Tariff An.)   money in question   ─┘
                                              │
                                              ▼
advocacy         (Provider Advocate)      when a reading supports fraud — receives the four readings
                                              │
                                              ▼
review           (Audit Reviewer)         every case — receives all five
                                              │
                                              └── callback: evidence rules + disputes → policy.decide()
                                                      │
                                                      ▼
            committee_brief (Committee Liaison, on a referral) ──▶ enforce (Enforcement Officer)
```

**Which tasks run.** `mandatory_tracks(case)` derives from the trigger and the file the tracks the rules require —
the clinical track on T2, T3, T4 and T7; the field track when reports are on file; the money track on R2 and on any
claim above its package rate. The Case Router's plan is added to that set and can never subtract from it (B-48), and
the router may not open the field track when no report exists. The advocate is not mandated by any trigger: it runs
when a reading supports fraud, or when the router opened it (B-49).

| Agent | Works | Tools (`crew/agent_tools.py`) | Returns |
|---|---|---|---|
| **Case Router** | every case, first | **none, by design** (B-48) | `RoutePlan`: the tracks it opens, each with a reason, and a summary |
| **Desk Investigator** | every case | `beneficiary_claim_history`, `documents_shared_with_other_claims`, `surgeon_same_day_claims`, `read_document`, `compare_documents` | `InvestigationReport`: stance, confidence, conclusion, citations |
| **Medical Auditor** | T2, T3, T4, T7, or the router | `beneficiary_claim_history`, `read_document` | `MedicalReport`: the same shape, on the clinical question |
| **Field Evidence Analyst** | field reports on file | `read_document` | `FieldReview`: one assessment per report on file |
| **Billing & Tariff Analyst** | R2, any claim above its package rate, or the router | `claim_tariff`, `read_document`, `beneficiary_claim_history` | `BillingReport`: the reading shape, on whether the amount is accounted for |
| **Provider Advocate** | when a reading supports fraud, or the router | `read_document`, `claim_tariff` | `AdvocacyReport`: the innocent explanation, whether the evidence `excluded` it, and citations |
| **Audit Reviewer** | every case | the five evidence tools | `AuditReview`: the findings it disputes, with reasons, and a summary |
| **Committee Liaison** | escalation, de-listing referral | `enforcement_decision`, `access_impact`, `file_committee_brief` | One sentence; the brief itself is committed through the tool |
| **Enforcement Officer** | every case | `enforcement_decision`, `access_impact`, and the action tools `release_claim`, `issue_show_cause_notice`, `order_field_audit`, `suspend_hospital`, `escalate_to_state_committee`, `refer_specialty_for_delisting`, `refer_for_human_review` | One sentence naming the action executed; the action itself is committed through the tool |

**What each tool reads.** Every tool is bound to one `CaseFile`: the claim, and the claims a tool has shown to be
related to it (same beneficiary, a byte-identical document, the same surgeon on the same day). `read_document` and
`compare_documents` refuse any other claim. `claim_tariff` computes the published rate, the amount claimed and the
excess, and lists the documents on file that could account for it — the figures, not a judgement about them. No
evidence tool says anything about network adequacy, so no agent that reads the evidence can know it. No tool shows
another hospital's identifier, only its district. Each call is recorded against the agent that made it, so the trail
says whose call it was.

**Independence, and the hand-off.** The four readers' tasks take no context (`context: []`), so none sees another's
finding, and each receives its own case file (`case_file(case, reader)`): the desk's, the auditor's and the analyst's
quote what that reader judges — the documents, the clinical record, the field reports (a report the desk had also read
would count twice, once as itself and once inside the reading) — and the billing file adds the package rate, the
amount claimed and the documents that could account for the excess. The router's file is thinner still: the trigger,
the package and rate, what kinds of document are on file, whether field reports exist, and the tracks already
mandatory. It never quotes a document.

Two tasks do take context. The advocacy task's is the four reading tasks, because the hospital's side is an answer to
what they found; the review task's is all five. CrewAI passes their outputs, each labelled with the finding it is
(`desk_audit`, `medical_audit`, `field_reports`, `billing_audit`, `advocacy`). A skipped conditional task passes
nothing.

**Between the agents.** Every task that produces a finding has a guardrail, and the review task has the callback that
decides. A guardrail returns feedback for one retry; a second failure fails the task, and the case degrades to the
rules. The checks are on substance, not shape (B-50), because a well-formed answer nothing supports passes a schema
(F-63):

1. the **route guardrail** requires each opened track to be a real track, with a reason in at least one full sentence,
   and refuses the field track when no report is on file;
2. the **reading guardrails** (desk, medical, billing) require a stance to cite what it rests on; require the money
   stance to have called `claim_tariff`, since figures not computed here are not figures this case establishes; and
   require a stance on a trigger about other claims to come after those claims have been listed and examined — T5, T6
   and T7 for the desk, T7 for the medical auditor, which lists the admissions and reads their clinical records. An
   honest `null` passes all of them;
3. the **advocacy guardrail** requires the explanation to be a full sentence, and an explanation the evidence does not
   exclude — the answer that stops an action — to cite a document and to have been read off the file with
   `read_document` or `claim_tariff`;
4. the **review guardrail** requires each dispute to name a finding that exists on this case (`desk_audit`,
   `medical_audit`, `billing_audit`, `advocacy`) and to give its reason in at least one full sentence, and refuses a
   summary that asserts a dispute the list omits — only the list sets anything aside;
5. the **callback** (`decide`) turns the readings into findings through the evidence rules
   (`guardrails.guarded_findings`):
   - the rules desk's preconditions bind the desk's reading and the medical auditor's;
   - field reports keep their recorded stance: the analyst may lower a report's confidence, never raise or flip it,
     and a report it omits keeps the field team's own;
   - the registry finding is the lookup's;
   - a **disputed** finding is set aside: stance `null`, confidence 0, the reviewer's reason appended to the
     conclusion, and the finding recorded in `Decision.disputed`;
   - a dispute of a reading that rests on the claim store's own byte-identical comparison is **refused** and recorded
     in `disputes_refused` (B-44a): the reading keeps its weight, because removing it would raise the survivor's
     confidence by subtraction (F-61).

   The defence reaches the policy only if it stands — unexcluded by the evidence and not disputed away — and then
   `policy.defended` turns a show-cause notice or a suspension into a field audit, or into a human review when no
   field channel is left to order (`DEFENCE_UNEXCLUDED`). It can never produce a release, and an escalation or a
   de-listing referral stands, because those already put the decision to the Committee.

   It then calls `policy.decide()`. Nothing the liaison or the officer does can change the result.

**Briefing the Committee.** On an escalation or a de-listing referral, `file_committee_brief` checks that the
decision refers the case to the Committee, that no brief has been filed, that fewer than three have been rejected, and
that the summary, the two to four options with their consequences, the recommendation and the question all pass the
publication checks (`brief_problems`, which runs `draft_problems` over each part). A rejected brief goes back to the
liaison with its reasons; the trail records the lengths and the problems, never the rejected words. Without a filed
brief the artefact keeps its standard question.

**Acting.** Each action tool checks, in order:
- that the policy has decided;
- that nothing has been executed yet;
- that its action is the policy's;
- that fewer than three drafts have been rejected;
- that the explanation, and any field questions or documents requested, pass the publication checks
  (`draft_problems`, `item_problems`).

A refusal is raised as an error, which CrewAI hands back to the officer as the tool's result. On success the tool
commits the action to the case. `process()` then writes it through `crew/actions.py`, still the only module with side
effects. The action tools are never cached, so a repeated call runs every check again.

**When something fails.**

| Failure | Outcome |
|---|---|
| Any reader or the reviewer fails, or ignores its guardrail twice | The policy never decides: the rules investigate; `degraded = True`, and `agents`, `review`, `disputed`, the router's tracks, the defence and the brief are left empty because the rules decided the case |
| The router fails, or ignores its guardrail twice | The same: the case degrades. The router runs first, so nothing it opened is half-worked |
| The router opens nothing | Every track the rules mandate still runs: its plan can only widen (B-48) |
| The advocate fails, or files no defence | The decision stands on the readings; nothing is stopped, because only an explanation actually put and unexcluded stops an action |
| The liaison fails or files nothing | The decision stands; the artefact carries the standard question for the Committee |
| The officer fails, or never acts, after the policy decides | The decision stands; the orchestrator executes it with the template explanation (`acted_by = "orchestrator"`) |
| Every model call fails (S9) | Degraded, as before |

**The model protocol** (`crew/gemini.py`, `crew/llm.py`). CrewAI 1.15.20 passes tools in OpenAI shape and expects a
list of calls back; it runs the tools itself and appends the results.
- **Gemini 3** rejects a function-call turn resent without its thought signature. **Claude with adaptive thinking**
  rejects a tool-use turn resent without its thinking blocks.
- CrewAI rebuilds those turns without either, so each adapter keeps the turn as the model returned it, keyed by call
  id, and resends it verbatim.
- Tool results become one user turn: Gemini `function_response` parts, or Anthropic `tool_result` blocks first,
  followed by any text.

Verified live on Gemini with a CrewAI agent calling tools in parallel, including a hand-over from `gemini-3.8-flash` to
the backup model mid-loop. Offline, `tests/fakes.py` plays all nine agents through each adapter and CrewAI's real loop.

**Structured output.** Each reader and the reviewer finish with a JSON object. After a guardrail, CrewAI converts only
what the guardrail returns, so every guardrail parses the answer, tolerating a code fence or a sentence around it, and
returns clean JSON — for the readers, with the label the reviewer reads it by.

**Who worked the case.** The crew's task callback records each agent whose task ran to completion, in order, on
`Decision.agents`; a skipped conditional task records nothing. Beside it the Decision carries what those agents did
that the findings alone do not show: the tracks the router opened that no rule mandated (`opened_by_router`) and why
(`router_reasons`), the reviewer's summary (`review`), what it disputed (`disputed`) and what was refused
(`disputes_refused`), and the hospital's side (`defence`, `defence_excluded`). The artefact names the roster under the
header, the decision log lists it in `agents`, and a degraded decision lists none.

---

## 7. Orchestration (`crew/run.py`)

```python
def process(claim: Claim) -> Decision:
    hit = triggers.evaluate(claim)                    # deterministic
    if hit is None:
        return actions.release(claim, ["NO_TRIGGER"])

    try:
        channels  = crew_triage(hit)                  # THE BRANCH
        findings  = []
        if Channel.DESK_AUDIT in channels:
            findings.append(crew_desk_audit(claim, hit))
        findings.append(crew_registry_verification(claim))
        if needs_field(channels):
            findings.append(stub_field_evidence(claim, channels))
        degraded = False
    except (LLMUnavailable, ValidationError) as e:
        findings = rules.deterministic_findings(claim, hit)   # NFR-1
        channels = hit.channels
        degraded = True
        log.warning("degraded path: %s", e)

    adequacy = tools.district_adequacy(claim.district_code, claim.specialty_code)
    decision = rules.policy.decide(claim, hit, findings, adequacy, degraded)
    actions.execute(decision)                         # side effects live here
    actions.append_log(decision)
    return decision
```

Three properties this shape guarantees:

1. **`decide()` is pure** — unit-testable with no LLM, no I/O, no network.
2. **The degraded path is a real code path** (NFR-1), which is why Test 9 is a genuine test rather
   than a mock.
3. **Side effects are isolated in `actions.py`** — the LLM cannot issue a notice; only the executor
   can, and only on a validated `Decision`.

---

## 8. Actions (`crew/actions.py`)

| Action | Effect | Artefact |
|---|---|---|
| `RELEASE_CLAIM` | Clear the withhold flag | log row |
| `WITHHOLD_CLAIM` | Set withhold, record reason codes | log row |
| `SHOW_CAUSE` | Render Annexure 4 template, start the 5-day clock | `out/artefacts/notice_<case>.md` |
| `FIELD_AUDIT` | Append to the SAFU queue with the checklist | `out/queue/field_audit.jsonl` |
| `SUSPEND` | Set suspended, refer to SEC | `out/artefacts/suspension_<case>.md` |
| `DELIST_SPECIALTY` | Remove specialty, flag district uncovered | `out/artefacts/delist_<case>.md` |
| `ESCALATE_SEC` | **Assemble evidence pack with access impact** | `out/artefacts/escalation_<case>.md` |
| `NO_ACTION` | Log only | log row |
| `REFUSE` | Log with the stated reason | log row |

### 8.1 Escalation brief — required sections

1. Case identifiers and the trigger that fired
2. Findings per agent, with citations
3. Conflicts between agents, unresolved ones stated as such
4. **Access impact:** providers in district, km to nearest alternative, population affected,
   aspirational status, capability flag
5. The action the system would have taken had alternatives existed
6. The specific question put to the SEC

**As built (§6a):** sections 1 to 5 are rendered from the `Decision`, and section 6 is the **Committee Liaison's
brief** — the evidence and the access consequence side by side, two to four options open to the Committee with their
consequences, a recommendation and the question — filed through `file_committee_brief` and checked like a notice.
When no brief was filed (the liaison failed, or three briefs were rejected), the artefact carries the standard
question for that gate instead.

---

## 9. Error handling

| Condition | Behaviour | Decision field |
|---|---|---|
| LLM endpoint unavailable | Deterministic findings, continue | `degraded = True` |
| Agent returns schema-invalid output, or ignores its task guardrail | The guardrail hands back what was wrong and the agent works again; a second failure fails the task, and the rules decide the case (§6a) | `degraded = True` |
| Agent output is blocked or truncated by the model | The adapter raises; the crew stops and the rules decide | `degraded = True` |
| A reading the Audit Reviewer disputes | Set aside: stance `null`, confidence 0, the reason on the finding. What the case does next depends on the readings left standing | `disputed` |
| A dispute of a reading that repeats a comparison the store makes itself | Refused: the reading keeps its stance and confidence, and the refusal is recorded on the finding and in the artefact (B-44a) | `disputes_refused` |
| Drafted explanation fails `draft_problems()` | The action tool refuses and hands the reasons to the officer, which revises (at most three drafts); if none passes, the orchestrator ships the template | `acted_by = orchestrator` |
| A Committee brief fails `brief_problems()` | `file_committee_brief` refuses with the reasons, and the liaison revises (at most three); without a filed brief the artefact carries the standard question | logged in the trail |
| `district_name` resolves to ≥2 LGD codes, or none | Refuse | `action = REFUSE` |
| `package_code` not in HBP master, or `hospital_ref` not in the registry | Refuse | `action = REFUSE` |
| Missing `discharge_ts`, discharge before admission, or submission before discharge | Refuse | `action = REFUSE` |
| Access unknowable (registry lists nothing, or no district data) | Escalate rather than suspend | `gate = unknown` |
| `hospital_ref` not pseudonymous; claim id not a plain token; unknown field; document hash not matching its text | Raise at validation (again in `process()`, for claims built with `model_copy`) | never reaches the pipeline |
| `district_name` resolving to a different district than `district_code` | Refuse | `action = REFUSE` |
| A field report on the desk channel, or two reports on one channel | Refuse | `action = REFUSE` |
| `--out` names a directory holding files the run did not write | Refuse to start | exit with an error |

JR, TG and OT are not scored for adequacy (`build_reference.py`, `UNSCORED`); no published package maps to them,
so no claim can bill them. (The original design's `RARE_SPECIALTY` skip was never needed and does not exist; F-34.)

**Never guess.** A fabricated district code silently corrupts the access gate, which is the one
thing the whole project rests on.

---

## 10. Test hooks

- `rules.policy.decide()` is pure — every branch is unit-testable without the crew
- `triggers.evaluate()` is pure — table-driven tests over crafted claims
- `crew.run.process()` accepts an injected `llm=None` to force the degraded path
- Every run takes a seed; `out/decision_log.csv` is byte-comparable across runs (NFR-4)

See [05-TEST-PLAN](05-TEST-PLAN.md).
