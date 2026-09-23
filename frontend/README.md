# The Access Gate — front end

An evidence viewer for the decisions `access-gate/` has already made. It shows a human **what the
system decided and why**: what was flagged, who looked at it, what they found, who disagreed, what
was decided, and what it would have done to access in the district.

It performs no fraud detection, runs no agents and changes no decision. It reads static JSON, makes
no request at runtime, and works with the network off.

```
access-gate/              the repository root — the Python system
├── scripts/export_frontend.py    the only thing this app added to it
└── frontend/             this app
```

## Run it

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

```bash
npm run build          # a fully static site in build/
npm run preview        # serve build/ locally
npm run check          # svelte-check, TypeScript strict
npm test               # vitest
npm run verify         # check + test + build, in that order
npm run export         # regenerate static/data from the decided run (see below)
npm run format         # prettier; the generated files are in .prettierignore
```

`npm run export` shells out to `python -m scripts.export_frontend` in the repository root. It needs
**no virtualenv and no installed package** — the exporter is standard library only, so whatever
`python` is on PATH will do.

`build/` is plain files. Serve it from anything — `npm run preview`, or `python3 -m http.server`
inside `build/` — and it needs no Node process, no database, no API key and no internet. The fonts
are bundled and the data is baked into the pages, so nothing is ever requested from another origin.
(The bundle does contain `svelte.dev/e/...` strings — they are Svelte's error-message links, printed
in a message if one ever fires, never fetched — and the usual `w3.org` XML namespaces.)

**Opened straight off disk** (`file://`), every page still renders completely — the prerendered HTML
carries all of it — but browsers refuse to load ES modules from `file://`, so nothing hydrates and
the filters, sorting and theme toggle are inert. The app detects that and says so in a banner rather
than leaving dead controls on screen. For a demo recording, serve the folder.

## Regenerate the data

The app reads four files under `static/data/`. They are produced by one script, which is the only
thing added to `access-gate/`:

```bash
cd ..                                                      # the repository root
python -m crew.run --scenarios --out out_live              # the ten demo scenarios
python -m scripts.export_frontend                          # writes frontend/static/data
```

The export reads `out_live/decision_log.csv`, the artefacts it issued, `out_live/field_audit_queue.jsonl`
and the committed `results/`, and writes:

| File              | What it holds                                                             |
| ----------------- | ------------------------------------------------------------------------- |
| `cases.json`      | one entry per decided case, with its artefact, roster, findings and trail |
| `evaluation.json` | the 5,000-case evaluation and the sweeps                                  |
| `benchmark.json`  | the rules-versus-crew benchmark, with every case                          |
| `meta.json`       | when it was generated, the model, the counts                              |

It also writes `src/lib/types.ts`. **Do not edit that file by hand** — it is generated, and
`src/tests/export-contract.test.ts` checks the committed JSON against it. A mismatch between the
export and the types is the most likely way this build breaks, so it fails in the test suite rather
than quietly rendering an empty state.

## The map

`src/lib/india-map.json` holds India as SVG path data: 36 states and union territories, the six
districts the demo run refers to, and the national outline. It is **committed and generated**, like
`types.ts`, and imported as a module rather than fetched — so the geometry ends up inside the
prerendered HTML and the map draws with the network off _and_ with JavaScript off.

```bash
cd ..                                   # the repository root
python -m scripts.build_india_map       # fetches the sources once, then builds
python -m scripts.build_india_map --check   # fails if the committed file is stale
```

Boundaries come from **DataMeet** (`github.com/datameet/maps`, MIT), pinned to one commit and
checked against a recorded SHA-256: its state layer is digitised from the Survey of India state map
and its districts from the 2011 Census. That source was chosen for its **boundary depiction** — it
shows the full extent India claims, which Natural Earth and most Western sources do not. The 2011
vintage means Jammu & Kashmir is drawn as one state; the evaluation page says so under the map.

The sources are ~56 MB and are **not committed**; the script downloads them into a gitignored
`.mapcache/` on first run and works offline after that (`--offline` refuses to fetch at all). The
build is deterministic — same sources, same output, byte for byte — so `--check` is a real check.
`tests/test_india_map.py` reads the committed output, so the Python suite still runs in a bare
checkout with no cache and no network.

There is no map library and no tile server. Projection (Albers equal-area conic), simplification and
path generation all happen in that script; `src/lib/map.ts` only does arithmetic on the result.

### Running it with the agents

`python -m crew.run --scenarios` is deterministic and needs no API key, but on that path the model
never runs, so **every case comes back `degraded`**: the rules decided alone, and there is no roster,
no dispute, no hospital's side and no investigation trail. The app handles that shape explicitly and
says so on screen, but it is a thin demo.

For a run with the crew, put a key in `access-gate/.env` and:

```bash
cd ..                                                      # the repository root
python -m crew.run --scenarios --llm --provider gemini --out out_live
python -m scripts.export_frontend
```

Then rebuild. Nothing in the front end changes; the same components fill with the crew's work.

The `/benchmark` page is unaffected either way: it reads the committed `results/`, which already hold
a real nine-agent run.

## What the screens are

| Route              | What it is                                                                                                                                                                                           |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`                | the case list — every decided case, filterable by action group, gate, trigger, district and degraded, with the districts marked on a map                                                             |
| `/case/[claim_id]` | the case record, top to bottom: trigger, roster, findings, the hospital's side, the review, **the access gate**, the trail, the artefact — with an index alongside that marks the section being read |
| `/evaluation`      | the 5,000-case evaluation: headline, the distance-threshold sweep and slider, where the Committee's load falls, sensitivity, ablation                                                                |
| `/benchmark`       | 40 hand-written cases through both paths, scored asymmetrically                                                                                                                                      |

## Things the code is careful about

These are the properties most likely to be broken by a well-meant edit, so each has a test.

- **Nothing is re-derived.** The action, the gate, the confidence and the aggregation were settled by
  `rules/policy.py`. `src/lib/domain.ts` maps them to labels and formats numbers; it never recomputes
  confidence, re-applies the floor, or infers an action from the findings. Any disagreement between
  the JSON and the screen is a bug in the export.
- **A null gate is not `clear`.** The gate is only computed for actions that would remove a provider.
  A case that never reached it renders as "not computed", with the reason.
- **`km_to_alternative: null` means "none listed"** — no alternative anywhere in the data, which is a
  _stronger_ access signal than a large number. It is never rendered as a blank.
- **A refused dispute is not a dispute.** `disputed` set a reading aside so it weighs nothing;
  `disputes_refused` tried to and was overruled, and that reading still counts. They render
  differently.
- **A degraded case is never presented as the crew's work.** It has no agent data at all, and the
  page says why rather than drawing an empty crew.
- **`refuse_malformed` has no trigger, no gate and no findings**, and every component that touches
  those handles their absence.
- **Confidence never appears alone.** Every display carries the fixed 0.70 floor, in the bar and in
  the accessible label.
- **The Router may only widen.** The roster distinguishes mandatory from router-opened work and
  carries the Router's stated reason; agents that did not run stay visible.

## Design notes

An instrument of record: ink on paper, dense, typographic. No gradients, no illustration, and no
decoration that is not carrying information.

- **The shell.** A fixed 252px rail on the left and a canvas that takes everything else. The rail
  carries the sections, the run being shown — its source, model, case counts, the confidence floor
  and the distance threshold — and the theme control, so the two constants every figure is read
  against never scroll away. It is the one dark surface in the light theme, which separates
  navigation from the record without spending a hue that a decision or a gate state might need.
  Below 1000px it becomes a bar with a drawer; with no JavaScript the drawer is open, because a
  button that cannot work is worse than a list that is always there.
- **Width.** The canvas is capped at `--col` (1680px) rather than at a reading measure, so on any
  ordinary display the app runs edge to edge instead of sitting in a column with empty margins.
  Prose inside it is still held to `--measure` (74ch) by a global rule, and the two panels built
  around the map cap the map itself rather than giving it a share of the width — as a percentage it
  kept growing until a 1,700px screen left a short list stranded beside a 760px-tall map.
- **Depth is a hairline and one faint shadow** (`--lift`), used only to lift a panel off the paper.
  It is deliberately too weak to read as a card floating in a dashboard, and on the dark theme it is
  almost entirely the hairline, because a shadow on a dark ground shows nothing.
- **Type.** IBM Plex Sans for text, IBM Plex Mono for identifiers, reason codes and tool names, IBM
  Plex Serif for the issued artefact, which is set as the legal-style instrument it is. All three are
  bundled from npm (`@fontsource`) and never fetched at runtime.
- **Colour is semantic only, and never the only signal.** Every action group, gate state and benchmark
  outcome also carries a label and a shape, so the record survives a projector, colour-blindness and a
  grayscale screen recording. Chart colours were checked with the data-viz palette validator for
  lightness band, chroma, CVD separation and contrast in both themes.
- **The gate gets the boldest treatment in the app.** When a suspension was diverted to protect a
  district's only provider, that is what the eye lands on: the case page says so from its top edge
  before the gate's own section arrives, the gate's amber is the rail's mark and the accent on the
  one control the app offers, and the index beside a case record marks that section and no other.
- **Light and dark**, following `prefers-color-scheme`, with a toggle that overrides it.
- Semantic HTML, real table semantics, focus states, keyboard-operable sort and filters.

## On `npm audit`

`npm audit --omit=dev` reports **0 vulnerabilities**, which is the number that describes this app:
every dependency is a devDependency, and the thing that ships is prerendered HTML, CSS, fonts and
JSON. A plain `npm audit` reports five, and none of them reach a user:

| Advisory                        | Why it cannot reach the built site                                                                                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `cookie`, via `@sveltejs/kit`   | SvelteKit's **server** runtime. This build is `adapter-static` and fully prerendered — there is no server, and the string `cookie` does not appear anywhere in `build/`. |
| `@vitest/mocker` path traversal | The test runner. It never runs outside `npm test`.                                                                                                                       |

**Do not run `npm audit fix --force` here.** npm's resolver "fixes" these by proposing
`@sveltejs/kit@0.0.30`, `@sveltejs/adapter-static@0.0.17` and `vitest@5.0.1` — downgrades of several
major versions that would not build. If the advisories matter to you, upgrade forward instead, and
re-run `npm run verify`.

## Regenerating from scratch

```bash
git clone <repo> && cd access-gate
python -m scripts.export_frontend     # standard library only, and out_live/ is committed:
                                      # no pip install, no API key, no pipeline run
cd frontend && npm install && npm run build
```
