# The Access Gate

**An agent crew for PM-JAY hospital-fraud enforcement, built on CrewAI.**

An agentic system that investigates suspected fraud by hospitals in India's national health
insurance scheme (Ayushman Bharat PM-JAY), acts on it autonomously — and **stops** when acting
would leave a district without the specialty it depends on.

> **The thesis in one sentence.** The system may conclude that a hospital committed fraud, but it
> may not decide that a district loses its only cardiac hospital.

---

## Why this project

NHA already automates fraud *detection* — the National Anti-Fraud Unit flags suspicious claims
within 24 hours and withholds payment. What it does not do is connect **network adequacy** to the
**enforcement decision**.

NHA's own empanelment guidelines (§3.2.3) permit relaxing criteria "based on local context,
*availability of providers, and the need to balance quality and access*", and elsewhere list the
adequacy indicators a State Empanelment Committee should watch — *specialties in various districts*,
*geographic distribution of empanelled hospitals*. **No threshold is attached to any of them, and
they never appear in the disciplinary process.**

We supply the missing measurement.

| | |
|---|---|
| **19.1%** | of district × specialty cells have exactly **one** empanelled provider |
| **51 km** | median distance to the nearest alternative district |
| **27.0%** | sole-provider rate in NITI aspirational districts, vs 17.9% elsewhere |
| **2,359** | hospitals de-empanelled to date; 1,200+ suspended |

---

## The demo, in one line

Identical fraud pattern at two hospitals. **Ahmedabad** has 101 cardiology providers — the system
suspends automatically. **Bahraich** has one, serving 4.16 million people, 83 km from the nearest
alternative, in an aspirational district — the system refuses to decide and escalates to a human,
showing exactly who would lose access.

---

## How the crew works

One CrewAI crew (`crew/crew_llm.py`; agents and tasks in `crew/config/*.yaml`). Nine agents, each owning one
question no rule can answer, with the tools that question needs. One routes the case, four read the evidence, one
puts the hospital's side, one checks what they all found, and two act on the decision the policy makes between them.

0. **The Case Router** — which of the optional investigations does this file raise? It sees the trigger, the rate and
   what *kinds* of document are on file, never the documents themselves, and it holds no evidence tool: a router that
   has read the case has already reached the question its readers are supposed to reach independently. The rules
   compute a mandatory set of tracks from the trigger; **the router's plan can only add to that set, never take from
   it.** A model deciding what not to investigate would be a model deciding the case.
1. **The Desk Investigator** — are the documents present, consistent, genuine, and not copied from other claims?
   - **Tools:** `beneficiary_claim_history`, `documents_shared_with_other_claims`, `surgeon_same_day_claims`,
     `read_document`, `compare_documents`. It decides what to fetch: three "separate" admissions with the same vitals
     chart only show when set side by side.
   - **Reach:** only this claim, and the claims its tools have shown to be related to it.
2. **The Medical Auditor** — was the care billed clinically needed? It works the triggers that turn on a clinical
   question (T2, T3, T4, T7), reads the record as a clinician, and never sees the desk's finding: two readings that
   saw each other would be one reading.
3. **The Field Evidence Analyst** — what is each field report on file worth? It may discount a vague report that
   answers a different question; it can never overturn what a field team recorded.
4. **The Billing & Tariff Analyst** — is the amount claimed actually accounted for? It computes the excess over the
   published package rate with `claim_tariff`, reads the invoices and bills that might explain it, and says how much
   is left unexplained. An invoice on file is not an explanation of an amount: ₹12,000 of implant does not account
   for a ₹60,000 excess, and that arithmetic is what BCH-R2-06 — the one case both paths got wrong — turns on.
5. **The Provider Advocate** — what innocent explanation will this file bear, and does the evidence exclude it? Before
   a hospital is acted against, someone puts its side. An explanation the evidence does **not** exclude turns a
   show-cause notice or a suspension into a field audit or a human review; it can **never** release a claim, and the
   Audit Reviewer may dispute it like any other reading. It can slow a case down, not decide that fraud did not
   happen.
6. **The Audit Reviewer** — does each reading hold against the evidence it cites? It receives every reading and may
   **dispute** one. A disputed reading weighs nothing, and what the case does next depends on the readings left
   standing — except that it may not set aside a reading that merely repeats a comparison the claim store makes
   itself (B-44a). It cannot convict or clear: doubt is all a review adds.
7. **The enforcement policy decides** (`rules/policy.py`, deterministic), on the readings after the evidence rules and
   the disputes (`crew/guardrails.py`). The access gate lives here.
8. **The Committee Liaison** prepares the State Empanelment Committee when a case is referred to it: the evidence and
   the access consequence side by side, the options genuinely open to the Committee, a recommendation, and the one
   question it must answer. It recommends; the Committee decides.
9. **The Enforcement Officer** carries the decision out.
   - **Reads:** the decision with `enforcement_decision`, and what it would do to access with `access_impact`.
   - **Writes:** the explanation, the field team's questions, or the documents a hospital must produce.
   - **Executes:** through the one action tool that accepts the policy's decision. Every other action tool refuses,
     and a draft that overstates the action or the access loss is sent back with the reason.

**Blind spot, by design:** no agent that reads the evidence can see who depends on the hospital. Only the two that act
on the decision read the access facts.

Every artefact and log row carries the **investigation trail**: each tool the agents called, with refused calls marked,
and the agents that worked the case.
Without a model (`--llm` off, or an outage), the same pipeline runs on the rules (`crew/investigate.py`), and the
decision says so.

**Does the crew read better than the rules?** [10-AGENT-EVALUATION](docs/10-AGENT-EVALUATION.md) runs 40 hand-written
cases through both paths. In these cases the truth is in free text or across claims: paraphrased justifications,
keyword traps, mislabelled evidence, copied records.

---

## Measured

A simulated year of flags on the real network (5,000 cases; placement from official
statistics, conduct simulated), run through the real pipeline. Full write-up:
[09-EVALUATION](docs/09-EVALUATION.md).

| | |
|---|---|
| **86.7%** | of flags reach a final action with no human |
| **0.63%** | of innocent flags end in suspension (0.44–0.63% across four corpus seeds: every one is a false flag that **both** field reports got wrong) |
| **10.9%** | of confirmed egregious findings would have left a district without a real provider of some specialty — escalated or referred instead, about **895 a year**. Suspension removes a hospital from every specialty it is empanelled for, so the gate checks them all |
| **18,251 → 1,016** | wrongful suspensions a year, before and after the two design flaws the evaluation found |

The registry is validated against the Ministry's own count: 31,196 vs 30,957
hospitals (+0.8%).

**The agents, measured.** The benchmark has 40 hand-written cases whose truth sits in the documents, run through
both paths of the same pipeline. The rules make **15 wrong decisions**. The crew has been run live five times:

| Run | Model | Cases scored | Wrong |
|---|---|---|---|
| Two agents (17 Sep) | `gemini-3.5-flash-lite` | 40 | **5** |
| Six agents (18 Sep) | `gemini-3.5-flash-lite` | 40 | **2** |
| Six agents, after F-61 | `gemini-3.8-flash` | 39 of 40 | **0** |
| Nine agents (pre-hardening) | `gemini-3.8-flash` | 39 of 40 | **0** |
| **Nine agents, hardened** (current) | `gemini-3.8-flash` | 39 of 40 | **0** |

**Read that table with its confound in view.** The last three runs were served by the primary model throughout, the
first two by the fallback, because the free tier's daily cap on the primary was gone by then. So the improvement from
two wrong to none is **not** attributable to the crew changes alone: a model change moves with it. The one comparison
that holds the model fixed is six agents against nine, and there the result is unchanged at zero wrong — the nine-agent
crew held the score rather than improved it, because on these 40 cases there was nothing left to improve.

- **No wrong decision in any of the six ways a case can be written** — keyword, paraphrase, trap, mislabelled,
  contradiction and cross-claim — and no innocent hospital enforced against.
- **The benchmark is now saturated for the crew.** Forty cases can no longer tell these rosters apart. That is a limit
  of the measurement, not evidence that the crew is finished; harder cases are the next honest step.
- **One case degraded** (BCH-R3-02: the model failed mid-case), so the crew scored 39. It is excluded from the
  crew's score and shown as degraded, never as a pass. The rules decided it alone and **got it wrong** — a
  show-cause notice against an innocent hospital, which is the clearest illustration of what the crew is for.

**What the nine agents actually did**, on the 39 cases the crew ran:

- The **Case Router** opened work no rule mandated on **10** cases — the Billing & Tariff Analyst on 5, the Provider
  Advocate on 4, the Medical Auditor on 1. The branch is real: those agents would not otherwise have worked those
  cases, and the router has never removed one the rules required.
- The **Billing & Tariff Analyst** worked **12** cases and read the money correctly on all seven above-rate claims,
  including BCH-R2-06, where an implant invoice of Rs 12,000 against an excess of Rs 60,000 had defeated both paths in
  every earlier run.
- The **Provider Advocate** worked **22** cases and found an innocent explanation the evidence did not exclude on 2 —
  both claims that were being released anyway. **It has still never changed a decision.** Its guard against
  arguing frauds free is doing its job; whether it earns its cost is not yet shown, and the benchmark, which has no
  case where a defence should bite, cannot show it.
- The **Audit Reviewer disputed nothing** in this run, so the dispute-refusal rule of F-61 was never exercised live.
  It is proven by test, not by this benchmark.
- Cost: **20.1 model requests a case** (782 for the run), against 17.5 before the substance guardrails and 5.4
  for the two-agent crew. The guardrails are not free: an agent sent back to do the work costs a round trip.
- **One case degraded** (BCH-R3-02: the model failed, so the rules decided), and the rules got it **wrong** —
  a show-cause notice against an innocent hospital. It is excluded from the crew's score, because it is not
  the crew's decision; it is a fair picture of what the fallback path costs.

Every case, the corrections and each run side by side are in
[10-AGENT-EVALUATION](docs/10-AGENT-EVALUATION.md). The cases were written by the team that built the crew, which that
document discusses.

---

## Documents

Read in this order.

| # | Document | For |
|---|---|---|
| [00](docs/00-BRD.md) | **Business Requirements** | Everyone. The problem, stakeholders, scope, success criteria. |
| [01](docs/01-HLD.md) | **High-Level Design** | Everyone. System context, components, technology, decision boundary. |
| [02](docs/02-LLD.md) | **Low-Level Design** | Engineers. Schemas, tool signatures, agent definitions, policy constants. |
| [03](docs/03-DIAGRAMS.md) | **Diagrams** | Everyone. Context, agentic flowchart, sequence, **crew composition and task graph, the agent×tool matrix, the execution loop, and one case's real tool trace**, data flow, decision boundary. |
| [04](docs/04-DATA-DICTIONARY.md) | **Data Dictionary** | Engineers + validation. Every source, field and code. |
| [05](docs/05-TEST-PLAN.md) | **Test Plan** | Validation owner. Ten scenarios, acceptance criteria, reported metrics. |
| [06](docs/06-RISK-REGISTER.md) | **Risk Register** | Everyone. What can go wrong and who owns it. |
| [07](docs/07-TEAM-RACI.md) | **Team & ownership** | Everyone. Who owns which part of the system. |
| [08](docs/08-GLOSSARY.md) | **Glossary** | Everyone. PM-JAY and NHA terminology. |
| [09](docs/09-EVALUATION.md) | **Evaluation** | Everyone, for the report. Auto-resolution, threshold sweep, disparate impact, what the evaluation forced us to change. *Generated.* |
| [10](docs/10-AGENT-EVALUATION.md) | **Agent evaluation** | Everyone, for the Automation Argument. Rules versus the crew on 40 cases whose truth is in the documents. *Generated.* |

Full project specification (single page, rendered): the `access-gate.html` in the parent folder.

---

## Quick start

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
cp .env.example .env               # GEMINI_API_KEY for the demo, ANTHROPIC_API_KEY for the final run; no key needed without --llm

python scripts/build_reference.py      # rebuild reference tables + verify documented figures
python scripts/build_lookups.py        # district points + trigger catalogue
python scripts/build_packages.py       # package rates, every specialty listing, day-care list
python scripts/fetch_ogd.py            # 21 official tables from data.gov.in (needs DATA_GOV_IN_API_KEY; already in repo)
python scripts/build_state_context.py  # official tables -> state context, specialty volumes; reconciled
python -m crew.run --scenarios         # the ten scenarios, deterministic -> out/
python -m crew.run --scenarios --llm --provider gemini  # the crew on Gemini, free tier (demo; GEMINI_API_KEY)
python -m crew.run --scenarios --llm --provider claude  # the crew on Claude (final presentation; ANTHROPIC_API_KEY)
python -m crew.run --scenarios --llm --only S2,S3       # re-run chosen scenarios (saves free-tier calls)
python -m metrics.run                  # a simulated year of flags -> results/, docs/09-EVALUATION.md (~20 min)
python -m metrics.run --doc-only       # rewrite docs/09 from results/ without re-running
python -m metrics.benchmark            # the agent benchmark on the rules (seconds)
python -m metrics.benchmark --llm --provider gemini     # ... and on the crew (live; resumes if the quota runs out)
python -m metrics.benchmark --report   # results/agent_benchmark.json + docs/10-AGENT-EVALUATION.md
pytest                                 # 318 tests, ~6 min, no API key needed
```

**Verified:** CrewAI 1.15.20 resolves cleanly on Python 3.12 and 3.13 (135 packages, no torch).

`build_reference.py` **asserts** the seven headline figures quoted throughout `docs/`. If a filter
changes and a number drifts, the run fails rather than letting the report and the code disagree.

## Data

All source data, the three NHA rulebooks, and the generated reference tables are in this repo.
See [data/PROVENANCE.md](data/PROVENANCE.md) for the origin and status of every file.

```
data/
├─ registry/    PM-JAY export, 35,286 hospitals          REAL
├─ crosswalk/   district name -> LGD code, 99.3% placed  REAL
├─ district/    785 x 93 feature frame, NITI ADP list    REAL
├─ geo/         734 district boundaries                  REAL
├─ external/ogd/  21 Ministry tables via data.gov.in     REAL
└─ reference/   adequacy, distance, state context        generated
results/        evaluation outputs (metrics.run)         from a SIMULATED corpus
rulebooks/      NHA guidelines, anti-fraud guidebook,
                HBP 2022 master                          REAL
```

---

## Build order — as built

1. `rules/policy.py` — thresholds with written reasons. **Before any agent code.**
2. `crew/tools.py` — deterministic, testable with no LLM
3. `rules/triggers.py` — detection and channel routing
4. `generate/fixtures.py` — the ten scenarios, selected from real data and asserted
5. `crew/investigate.py`, `crew/run.py` — the rules desk and the orchestrator
6. `crew/llm.py`, `crew/gemini.py` — the model adapters, native tool calling included
7. `crew/agent_tools.py`, `crew/guardrails.py`, `crew/config/*.yaml`, `crew/crew_llm.py` — the crew last
8. `generate/corpus.py`, `metrics/run.py`, `generate/benchmark.py`, `metrics/benchmark.py` — measurement

Building in this order means the pipeline works deterministically before agents are added, so a
failing agent **degrades** rather than blocks — and the `degraded` path in Test 9 is a real code
path, not a mock.

---

## Two non-negotiables

**Provider identity is pseudonymised in every fraud scenario.** The registry contains real, named,
identifiable hospitals. Our outputs are accusatory. Real identities are used *only* for
network-structure computation, which is published fact. No real hospital is asserted to have done
anything. See [BRD §7](docs/00-BRD.md#7-ethical-constraints).

**Nothing is trained.** Simulated data is used as *input* only, never as
*training* data. Triggers are NHA's published rules; the policy derives from NHA's guidelines;
reasoning uses a pre-trained model. If anyone proposes fitting a classifier on generated claims,
refuse — it is unlicensed and methodologically empty.

---

## Sources

- [NHA — Guidelines on Hospital Empanelment and De-Empanelment](https://www.nitiforstates.gov.in/public-assets/Policy/policy_files/GNC509Q000048.pdf) (Dec 2021, 46 pp)
- [NHA — Anti-Fraud Framework Practitioners' Guidebook](https://cdnbbsr.s3waas.gov.in/s3169779d3852b32ce8b1a1724dbf5217d/uploads/2024/09/20240924831436164.pdf) (164 pp)
- [HBP 2022 package master](https://nhmladakh.in/HBP_2022.pdf)
- [NHA — Field Investigation and Medical Audit Manual](https://sha.kerala.gov.in/wp-content/uploads/2026/03/NHA_Field-Investigation-and-Medical-Audit-Manual_April-2020.pdf)
- [PIB — Anti-fraud system for AB-PMJAY](https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1847423)
- PM-JAY empanelled hospital export, hospitals.pmjay.gov.in, all-India, July 2026
