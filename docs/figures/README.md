# Figures

Every diagram in [03-DIAGRAMS](../03-DIAGRAMS.md), rendered. Each figure is a pair: `<name>.mmd` is the Mermaid
source (the master), `<name>.png` is what you put on a slide. The PNGs are rendered at 3× scale on a white
background, so they stay sharp in a deck, in print, and in the report.

| Figure | What it shows | Use it for |
|---|---|---|
| `1-system-context.png` | Who talks to the system and what crosses each boundary | Framing the problem |
| `2-component-architecture.png` | Five layers; only L3 holds agents, and the policy decides between them | "Where does the LLM sit?" |
| `3-agentic-flowchart.png` ★ | The branch the agents take (what evidence does this trigger need?) against the branch the policy takes (who else can treat these patients?) | **The deck.** The answer to "why CrewAI and not a script?" |
| `4-sequence-one-case-end-to-end.png` | Scenario S2 end to end: router, desk, advocate, reviewer, policy, liaison, officer | Walking one case aloud |
| `4a-crew-composition-and-the-task-graph.png` ★ | The CrewAI view: nine agents, nine tasks, and the `context` edges that are the hand-off | **The deck.** The crew itself |
| `4c-how-one-task-is-actually-executed.png` | CrewAI's tool loop with this project's guardrail retry and degrade path | "What happens inside one agent?" |
| `4d-a-real-trace-every-tool-call-on-one-case.png` | The 15 tool calls of a real live run, verbatim from `out_live/` | **The honest answer to "did the agents do anything?"** |
| `5-decision-boundary.png` ★ | What is automated, what the access gate stops, what the Committee decides | **The deck.** The project's contribution |
| `6-data-flow-and-provenance.png` | Which data is real, which is simulated, and where each figure comes from | Defending the numbers |
| `7-build-order-and-dependencies.png` | What had to exist before what | Process questions |
| `8-team-workflow.png` | How the five of us worked | Process questions |

Two more are built for slides rather than for the document. They carry fewer boxes, larger type and a
horizontal flow, and they have no corresponding section in 03-DIAGRAMS — the detailed versions live there.

| Figure | What it shows | Use it for |
|---|---|---|
| `slide-agentic-workflow.png` | The crew as six stages: route, read, defend, review, decide, act | **The deck.** One slide that explains the whole system |
| `slide-decision-flow.png` | What happens to a flagged claim, ending at the access gate | **The deck.** One slide that explains the contribution |

## Re-rendering

Edit the `.mmd` (or the Mermaid block in 03-DIAGRAMS.md, which is the same text), then:

```bash
npx -y @mermaid-js/mermaid-cli -i docs/figures/<name>.mmd -o docs/figures/<name>.png -b white -s 3
```

On Windows, point puppeteer at the Chrome that is already installed instead of letting it download one — pass
`-p puppeteer.json` containing:

```json
{ "executablePath": "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe", "args": ["--no-sandbox"] }
```

Two things that break a render, both learned here: a **semicolon inside a sequence-diagram message** ends the
statement and fails the parse (use `·` or a comma), and a `Note over` on the leftmost participant alone is clipped at
the edge (span two participants instead).
