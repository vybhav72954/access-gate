# 07 · Team, Ownership and RACI

**Principle:** every part of the system has one named owner, and there is always someone who can
answer for it without looking around the room.

---

## 1. Roles

| Role | Area | Owns |
|---|---|---|
| **Problem owner** | The problem and its sources | The one-page brief. Who faces the decision, how often, what data exists, what happens today. Every citation in the report. |
| **Argument owner** | The written case | The formal six-factor case, signed off on day 2. |
| **Crew engineer** | The pipeline | Tools, agents, runner, actions. Must be able to walk any line of the pipeline aloud. |
| **Policy owner** | The decision rules | `rules/policy.py` — every threshold and escalation trigger, and the reason for each. |
| **Validation owner** | Evidence and testing | The ten scenarios, decision log, threshold sweep, disparate-impact audit, failure log. |
| **Comms owner** | Presentation and assembly | Demo recording, deck, report assembly. Translating the access argument for a non-technical audience. |

**Six roles, five people.** Merge one of these two ways:

- **Preferred:** crew engineer also owns policy — but only if that person can defend the *numbers*
  as confidently as the code. The policy file is what reviewers attack first.
- **Otherwise:** problem owner also owns comms. The brief and the deck tell the same story anyway.

---

## 2. Source ownership

Every member must be able to defend every design choice. Three government documents
totalling several thousand pages is not a one-person reading load. **Assign one source per person.**

| Source | Pages | Owner | Must be able to answer |
|---|---|---|---|
| NHA Empanelment & De-Empanelment Guidelines | 46 | Problem | Where does §3.2.3 sit? What is in Annexure 1? Who decides de-empanelment? |
| NHA Anti-Fraud Practitioners' Guidebook | 164 | Argument | What are the four evidence channels? What does trigger 5 require? What are the timelines? |
| HBP 2022 package master | 3,801 | Crew eng. | What is S12? How do registry codes map to HBP 2022? |
| PM-JAY registry + district frame | — | Validation | How many hospitals after filters? Why exclude three states? |
| The project brief | 5 | Comms | What exactly is the demo deliverable? What does the simulated-data rule require? |

---

## 3. RACI by work product

**R** responsible · **A** accountable · **C** consulted · **I** informed

| Work product | Problem | Argument | Crew | Policy | Validation | Comms |
|---|---|---|---|---|---|---|
| Problem brief (1 p) | **A/R** | C | I | I | I | C |
| Automation argument (1–2 p) | C | **A/R** | I | **C** | C | I |
| Design sign-off | C | **A/R** | I | I | I | I |
| `rules/policy.py` | I | **C** | C | **A/R** | C | I |
| `crew/tools.py` | I | I | **A/R** | C | C | I |
| Claims generator | I | I | **A/R** | C | **C** | I |
| Agents + runner | I | I | **A/R** | C | I | I |
| Ten test scenarios | I | C | C | C | **A/R** | I |
| Threshold sweep | I | **C** | C | **C** | **A/R** | I |
| Disparate-impact audit | I | **C** | I | C | **A/R** | I |
| Failure log | I | I | C | I | **A/R** | C |
| Demo recording | I | I | **C** | I | C | **A/R** |
| Deck | C | **C** | C | C | C | **A/R** |
| Report assembly | **C** | **C** | C | C | C | **A/R** |
| Q&A rehearsal | R | R | R | R | R | **A** |

**Two rows to notice.** The policy owner is *consulted* on the automation argument — because the
written case and the code must say the same thing, and the commonest failure is a report claiming a
threshold the code does not implement. And the argument owner is consulted on the threshold sweep,
because that sweep is the evidence for the argument's central claim.

---

## 4. Working agreements

1. **No code before design sign-off.** Day-2 gate. The plan deliberately puts the
   argument first, and writing the policy before the agents is what stops thresholds nobody can
   defend from being encoded.
2. **`policy.py` is written before any agent code.** Non-negotiable, and it is a shared review — all
   five read it, because all five may be asked about it.
3. **Every figure traces to the data dictionary.** If a number is in the deck it is in
   [04-DATA-DICTIONARY](04-DATA-DICTIONARY.md), with the same filters applied.
4. **The failure log is written as you go**, not reconstructed on day 11.
5. **Build freeze at the end of day 10.** After that: testing, writing, rehearsing. No new features.
6. **Whoever wrote a threshold defends it aloud** in the day-12 rehearsal. If they cannot, the
   threshold changes or the reason gets written properly.

---

## 5. Who answers what

Rehearse these. Each question has a primary answerer.

| Question | Primary | Backup |
|---|---|---|
| "Isn't this really Path B?" | Policy | Argument |
| "Your claims are synthetic — what have you shown?" | Argument | Validation |
| "Why CrewAI rather than a script?" | Crew eng. | Policy |
| "Why is the threshold 50 km and not 30?" | Policy | Validation |
| "Is the district the right unit?" | Problem | Argument |
| "Does the hospital actually disappear?" | Problem | Argument |
| "Isn't NHA already doing this?" | Argument | Problem |
| "Why should we believe your access numbers?" | Validation | Crew eng. |
| "Walk me through this function." | Crew eng. | Policy |
| "What went wrong?" | Validation | Comms |

The last one is the question most teams dread and the one you should want. You have six closed
findings in the [risk register](06-RISK-REGISTER.md#3-issues-already-closed) — two of them, the OSM
denominator and the Punjab/Gujarat convention, are genuinely interesting stories about
interrogating your own analysis rather than trusting it.
