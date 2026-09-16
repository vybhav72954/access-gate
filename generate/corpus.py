"""A year of flagged claims, at scale, for the metrics in docs/05-TEST-PLAN.md §4.

The ten fixtures prove each path works. This corpus measures how often each
path is taken when flags arrive in realistic proportions.

REAL (published data)
  * which states claims come from      admissions 2024-25 by state (Rajya Sabha answer)
  * which specialties                  authorised admissions by specialty (answer of 30-06-2024)
  * which district within a state      weight = sqrt(providers of the specialty) x sqrt(population),
                                       checked against real district admissions in Uttar Pradesh
                                       (all 75 districts) and Gujarat (all 33); see docs/09-EVALUATION.md §2
  * which hospitals can receive them   the pseudonymised registry
  * what a package pays                the published HBP package master
  * how many flags are fraud           0.18% base rate (PIB PRID 1847423), 1% flag FPR, 90% recall

SIMULATED (assumptions, every one a CorpusConfig field, swept in the evaluation)
  * conduct, documents, field reports
  * which trigger a fraud or a false flag fires (uniform: no published breakdown exists)
  * how often an innocent explanation is on file at the desk
  * how often a field report points the right way

Every claim carries its package's mandatory documents, as the claim system requires at submission.

Nothing here is used to train anything. It is input data for measurement.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from crew.schemas import Channel, Claim, Document, FieldReport, Hospital, Package
from crew.investigate import DISCHARGE_SUMMARY, required_documents
from crew.tools import Tools
from rules.triggers import (ACUTE_MEDICAL, IMPOSSIBLE_SURGEON_KM, PRIVATE_TYPES, SURGICAL, ClaimStore,
                            billed_specialty, reserved_for)

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data/reference"
FY_START = datetime(2024, 4, 1, 9, 0)

FRAUD_TRIGGERS = ("T2", "T3", "T4", "T5", "T6", "T7", "T10", "R1", "R2", "R3")
# T6 fires only on a byte-identical document filed for two different beneficiaries. We assume it
# raises no innocent flags; a clerical mix-up that did would become a wrongful suspension (docs/09 §6).
INNOCENT_TRIGGERS = ("T2", "T3", "T4", "T5", "T7", "T10", "R1", "R2", "R3")
DOC_EXPLAINABLE = {"T2", "T3", "T4", "R2", "R3"}     # an innocent explanation can sit in the file

MECHANISM = {
    ("T2", True): "phantom_procedure", ("T3", True): "phantom_admission", ("T4", True): "stay_padding",
    ("T5", True): "ghost_surgeon", ("T6", True): "document_reuse", ("T7", True): "phantom_repeat_admissions",
    ("T10", True): "billing_after_death", ("R1", True): "specialty_misuse", ("R2", True): "upcoding",
    ("R3", True): "reserved_package_misuse",
    ("T2", False): "same_day_discharge", ("T3", False): "same_day_discharge", ("T4", False): "complication",
    ("T5", False): "registration_keying_error", ("T7", False): "genuine_chronic_illness",
    ("T10", False): "death_registry_error", ("R1", False): "capable_not_empanelled",
    ("R2", False): "implant_cost", ("R3", False): "government_referral",
}


@dataclass(frozen=True)
class CorpusConfig:
    n_flagged: int = 5_000
    seed: int = 20260916
    base_rate: float = 0.0018          # PIB PRID 1847423
    fpr: float = 0.01                  # BRD base-rate argument; swept 0.5%-5%
    recall: float = 0.90               # BRD base-rate argument; frauds the triggers miss never enter
    p_explained: float = 0.60          # ASSUMPTION: innocent flag's explanation is on file at the desk
    p_certificate: float = 0.80        # ASSUMPTION: a death certificate is on file for billing-after-death
    field_accuracy: float = 0.85       # ASSUMPTION: a field report points the right way
    field_conf: tuple[float, float] = (0.60, 0.95)   # ASSUMPTION: field report confidence, uniform

    def __post_init__(self):
        lo, hi = self.field_conf
        checks = {"n_flagged >= 1": self.n_flagged >= 1, "0 < base_rate < 1": 0 < self.base_rate < 1,
                  "0 < fpr < 1": 0 < self.fpr < 1, "0 < recall <= 1": 0 < self.recall <= 1,
                  "0 <= p_explained <= 1": 0 <= self.p_explained <= 1,
                  "0 <= p_certificate <= 1": 0 <= self.p_certificate <= 1,
                  "0 <= field_accuracy <= 1": 0 <= self.field_accuracy <= 1,
                  "0 <= field_conf low <= high <= 1": 0 <= lo <= hi <= 1}
        broken = [rule for rule, ok in checks.items() if not ok]
        if broken:
            raise ValueError(f"CorpusConfig out of range: {', '.join(broken)}")

    @property
    def fraud_share_of_flags(self) -> float:
        tp = self.base_rate * self.recall
        return tp / (tp + (1 - self.base_rate) * self.fpr)


@dataclass
class Case:
    no: int
    claim: Claim
    trigger: str
    fraud: bool
    mechanism: str
    explained: bool | None
    hospital: Hospital
    specialty: str
    state: str

    @property
    def noncompliant(self) -> bool:
        """R1 without fraud is still a breach of empanelment terms (CAG 11/2023 §4.5)."""
        return self.trigger == "R1" and not self.fraud


def state_key(state: str) -> str:
    s = " ".join(state.upper().replace("&", " AND ").split())
    return "DADRA AND NAGAR HAVELI AND DAMAN AND DIU" if s in ("DADRA AND NAGAR HAVELI", "DAMAN AND DIU") else s


class World:
    """Sampling frames built once from the reference data; reusable across configurations."""

    def __init__(self, tools: Tools):
        self.tools = tools
        sc = pd.read_csv(REF / "state_context.csv", keep_default_na=False)
        adm = pd.to_numeric(sc.admissions_2024_25, errors="coerce")
        self.state_weight = dict(zip(sc.state, adm.fillna(0)))
        sv = pd.read_csv(REF / "specialty_volume.csv", keep_default_na=False)
        self.specialty_weight = dict(zip(sv.specialty, sv.admissions.astype(float)))
        self.specialty_avg_rs = dict(zip(sv.specialty, sv.avg_claim_rs.astype(float)))

        pts = pd.read_csv(REF / "district_points.csv", keep_default_na=False)
        pts["pop"] = pd.to_numeric(pts.pop_now, errors="coerce").fillna(0)
        self.pop = dict(zip(pts.district_code.astype(int), pts["pop"]))
        self.codes = pts.district_code.astype(int).to_numpy()
        self.lat = np.radians(pts.lat.astype(float).to_numpy())
        self.lon = np.radians(pts.lon.astype(float).to_numpy())
        self.idx = {c: i for i, c in enumerate(self.codes)}

        # Population for the 23 districts without boundary geometry comes from the district network table.
        net = pd.read_csv(REF / "district_network.csv", keep_default_na=False)
        self.pop = {**dict(zip(net.district_code.astype(int), pd.to_numeric(net.pop_now, errors="coerce").fillna(0))),
                    **self.pop}

        self.providers: dict[tuple[str, str], dict[int, list[Hospital]]] = defaultdict(lambda: defaultdict(list))
        self.by_state_district: dict[str, dict[int, list[Hospital]]] = defaultdict(lambda: defaultdict(list))
        self.by_specialty: dict[str, list[Hospital]] = defaultdict(list)     # with geometry: T5 needs distances
        for h in sorted(tools.hospitals(), key=lambda h: h.hospital_ref):
            st = state_key(h.state)
            self.by_state_district[st][h.district_code].append(h)
            for s in h.specialties:
                self.providers[(st, s)][h.district_code].append(h)
                if h.district_code in self.idx:
                    self.by_specialty[s].append(h)
        self._lacking: dict[str, dict[str, dict[int, list[Hospital]]]] = {}
        self.packages: dict[str, list[Package]] = defaultdict(list)
        for p in sorted(tools.packages(), key=lambda p: p.package_code):
            for s in p.specialties:                  # every listing: a package is billable under each (F-35)
                self.packages[s].append(p)

    def lacking(self, specialty: str) -> dict[str, dict[int, list[Hospital]]]:
        """Hospitals that list specialties, but not this one, by state and district (R1's frame); cached.
        A hospital listing nothing cannot fire R1: a blank list is missing data (D-4)."""
        if specialty not in self._lacking:
            self._lacking[specialty] = {st: {d: [h for h in hs if h.specialties and specialty not in h.specialties]
                                             for d, hs in dist.items()}
                                        for st, dist in self.by_state_district.items()}
        return self._lacking[specialty]

    def km_from(self, district: int) -> np.ndarray:
        i = self.idx[district]
        h = (np.sin((self.lat - self.lat[i]) / 2) ** 2
             + np.cos(self.lat[i]) * np.cos(self.lat) * np.sin((self.lon - self.lon[i]) / 2) ** 2)
        return 2 * 6371.0 * np.arcsin(np.sqrt(h))


# ── package and specialty constraints per trigger ─────────────────────────

def _package_ok(trigger: str, p: Package, h: Hospital, fraud: bool = True) -> bool:
    """Whether billing `p` at `h` fires `trigger` and nothing else -- judged exactly as rules/triggers.py judges it:
    the specialty the hospital bills through, and the package's reservation as that hospital can bill it."""
    public = h.hospital_type in ("Public", "GOI")
    reserved = reserved_for(p, h)
    if trigger == "R3":
        # R3 needs a hospital known to be private. An innocent R3 flag is a legitimate government referral, which
        # the package master allows for only 9 reserved packages; anywhere else the billing is itself the breach.
        return reserved and h.hospital_type in PRIVATE_TYPES and (fraud or p.referral_allowed)
    if reserved and not public:
        return False
    spec = billed_specialty(p, h)
    if trigger == "R1":
        return not set(p.specialties) & set(h.specialties)
    if trigger == "T2":
        return spec in SURGICAL and p.is_major
    if trigger == "T3":
        return spec in ACUTE_MEDICAL and not p.daycare_candidate
    if trigger in ("T4", "T7"):
        return spec in ACUTE_MEDICAL
    if trigger == "T5":
        return spec in SURGICAL
    if trigger == "R2":
        return p.amount_rs > 0
    return True


def _specialty_pool(trigger: str, world: World) -> list[str]:
    if trigger in ("T3", "T4", "T7"):
        pool = ACUTE_MEDICAL
    elif trigger in ("T2", "T5"):
        pool = SURGICAL
    else:
        pool = set(world.specialty_weight)
    return sorted(s for s in pool if world.specialty_weight.get(s, 0) > 0 and world.packages.get(s))


class CorpusBuilder:
    def __init__(self, tools: Tools, cfg: CorpusConfig, world: World | None = None):
        self.cfg, self.world = cfg, world or World(tools)
        self.rng = np.random.default_rng(cfg.seed)
        self.support: list[Claim] = []

    def _choice(self, items, weights=None):
        if weights is not None:
            w = np.asarray(weights, dtype=float)
            return items[int(self.rng.choice(len(items), p=w / w.sum()))]
        return items[int(self.rng.integers(len(items)))]

    # ── where the claim happens ───────────────────────────────────────────

    def _place(self, trigger: str, fraud: bool = True) -> tuple[str, str, Hospital, Package]:
        w = self.world
        for _ in range(200):
            pool = _specialty_pool(trigger, w)
            spec = self._choice(pool, [w.specialty_weight[s] for s in pool])
            if trigger == "R1":
                frames = w.lacking(spec)
            else:
                frames = {st: w.providers[(st, spec)] for st in w.by_state_district if (st, spec) in w.providers}
            if trigger == "T5":      # an impossible-surgeon pair needs a distance, so both districts need geometry
                frames = {st: {d: hs for d, hs in dist.items() if d in w.idx} for st, dist in frames.items()}
            states = sorted(st for st, dist in frames.items()
                            if w.state_weight.get(st, 0) > 0 and any(dist.values()))
            if not states:
                continue
            st = self._choice(states, [w.state_weight[s] for s in states])
            districts = sorted(d for d, hs in frames[st].items() if hs)
            dw = [math.sqrt(len(frames[st][d])) * math.sqrt(max(w.pop.get(d, 0), 1.0)) for d in districts]
            d = self._choice(districts, dw)
            hosp = self._choice(sorted(frames[st][d], key=lambda h: h.hospital_ref))
            pkgs = [p for p in w.packages[spec] if _package_ok(trigger, p, hosp, fraud)]
            # An impossible-surgeon pair needs a provider of the specialty far enough away; without one the
            # draw below had nothing to choose from and the whole corpus build crashed.
            if pkgs and (trigger != "T5" or self._far_districts(spec, hosp)):
                return st, spec, hosp, self._choice(pkgs)
        raise RuntimeError(f"could not place a {trigger} claim")

    def _far_districts(self, spec: str, h: Hospital) -> list[int]:
        """Districts with a provider of `spec` far enough from `h` for the same surgeon to be impossible."""
        km = self.world.km_from(h.district_code)
        return sorted({o.district_code for o in self.world.by_specialty[spec]
                       if km[self.world.idx[o.district_code]] > IMPOSSIBLE_SURGEON_KM + 25})

    # ── claim construction ────────────────────────────────────────────────

    def _amount(self, p: Package) -> int:
        return p.amount_rs if p.amount_rs > 0 else int(self.world.specialty_avg_rs.get(p.specialty, 10_000))

    @staticmethod
    def _mandatory(cid: str, p: Package) -> list[Document]:
        """The package's mandatory uploads, every category the package master names (crew.investigate
        .required_documents): the claim system will not accept a claim without them. Texts are neutral on purpose, so
        they cannot satisfy or trip a desk rule by accident. The discharge summary is filed by the caller."""
        return [Document(doc_type=r.filed_as, text=f"Claim {cid}: {r.label} as listed for package {p.package_code}.")
                for r in required_documents(p) if r is not DISCHARGE_SUMMARY]

    def _claim(self, cid: str, h: Hospital, p: Package, ben: str, admit: datetime, los: int, **kw) -> Claim:
        docs = kw.pop("documents", None) or [Document(
            doc_type="discharge_summary",
            text=f"Discharge summary, claim {cid}, beneficiary {ben}. {p.package_name}. Discharged stable.")]
        docs = docs + self._mandatory(cid, p)
        return Claim(claim_id=cid, hospital_ref=h.hospital_ref, district_code=h.district_code,
                     package_code=p.package_code, beneficiary_ref=ben, admission_ts=admit,
                     discharge_ts=admit + timedelta(days=los, hours=6), amount_claimed=kw.pop("amount", self._amount(p)),
                     documents=docs, submitted_ts=admit + timedelta(days=los, hours=10), **kw)

    def case(self, no: int, trigger: str, fraud: bool) -> Case:
        cfg, rng = self.cfg, self.rng
        st, spec, h, p = self._place(trigger, fraud)
        cid, ben = f"CLM-C{no:06d}", f"BEN-C{no:06d}"
        admit = FY_START + timedelta(days=int(rng.integers(40, 365)))
        los = int(rng.integers(1, 6))
        explained = None
        if trigger in DOC_EXPLAINABLE and not fraud:
            explained = bool(rng.random() < cfg.p_explained)
        summary = Document(doc_type="discharge_summary",
                           text=f"Discharge summary, claim {cid}, beneficiary {ben}. {p.package_name}. Discharged stable.")
        docs, kw = [summary], {}

        if trigger in ("T2", "T3"):
            los = 0
            if explained:
                docs.append(Document(doc_type="lama_form", text=f"Claim {cid}: patient left against medical advice "
                                                                 f"(LAMA) on the day of admission; form signed."))
        elif trigger == "T4":
            los = int(rng.integers(11, 21))
            if explained:
                docs.append(Document(doc_type="clinical_notes", text=f"Claim {cid}: hospital-acquired pneumonia, a "
                                                                      f"complication on day 6; antibiotics escalated."))
        elif trigger == "T5":
            surgeon = f"REG-SIM-C{no:06d}"
            od = self._choice(self._far_districts(spec, h))
            other = self._choice(sorted((o for o in self.world.by_specialty[spec] if o.district_code == od),
                                        key=lambda o: o.hospital_ref))
            self.support.append(self._claim(f"{cid}-A", other, p, f"{ben}-A", admit, los, surgeon_reg_no=surgeon))
            kw["surgeon_reg_no"] = surgeon
        elif trigger == "T6":
            shared = Document(doc_type="discharge_summary",
                              text=f"Discharge summary. {p.package_name}. Procedure completed as planned; tolerated "
                                   f"well; follow up in OPD after two weeks. Ref {no:06d}.")
            docs = [shared]
            self.support.append(self._claim(f"{cid}-A", h, p, f"{ben}-A", admit - timedelta(days=int(rng.integers(3, 40))),
                                            los, documents=[shared]))
        elif trigger == "T7":
            for i, back in enumerate(sorted(rng.choice(np.arange(6, 29), size=2, replace=False)), start=1):
                self.support.append(self._claim(f"{cid}-P{i}", h, p, ben, admit - timedelta(days=int(back)), 1))
        elif trigger == "T10":
            died = admit - timedelta(days=int(rng.integers(5, 120)))
            kw["beneficiary_death_ts"] = died
            if fraud and rng.random() < cfg.p_certificate:
                docs.append(Document(doc_type="death_certificate", text=f"Certificate of death. Beneficiary {ben}. "
                                                                        f"Date of death {died:%d %B %Y}."))
        elif trigger == "R2":
            kw["amount"] = int(p.amount_rs * rng.uniform(1.25, 2.0))
            if explained:
                docs.append(Document(doc_type="implant_invoice", text=f"Claim {cid}: implant invoice, manufacturer "
                                                                       f"batch and MRP attached."))
        elif trigger == "R3" and explained:
            docs.append(Document(doc_type="referral_letter", text=f"Claim {cid}: referred by the district hospital "
                                                                   f"for want of the service."))

        claim = self._claim(cid, h, p, ben, admit, los, documents=docs, **kw)
        return Case(no=no, claim=claim, trigger=trigger, fraud=fraud, mechanism=MECHANISM[(trigger, fraud)],
                    explained=explained, hospital=h, specialty=spec, state=st)

    def field_reports(self, case: Case, channels: list[Channel]) -> list[FieldReport]:
        """What a field team brings back. Truth is the case's; accuracy and confidence are assumptions.

        Each (case, channel) draws from its own random stream, so a report never changes because some
        other case -- or another channel of this case -- was simulated differently. Ablation and
        sensitivity variants are therefore compared on identical draws (common random numbers).
        """
        lo, hi = self.cfg.field_conf
        out = []
        for ch in channels:
            u_right, u_conf = np.random.default_rng([self.cfg.seed, case.no, list(Channel).index(ch)]).random(2)
            supports = case.fraud if u_right < self.cfg.field_accuracy else not case.fraud
            out.append(FieldReport(channel=ch, supports_fraud=supports, confidence=round(lo + (hi - lo) * float(u_conf), 2),
                                   summary=f"Simulated {ch.value} report: evidence "
                                           f"{'consistent with' if supports else 'contradicts'} the flagged conduct."))
        return out


def build_corpus(tools: Tools, cfg: CorpusConfig = CorpusConfig(),
                 world: World | None = None) -> tuple[list[Case], ClaimStore, CorpusBuilder]:
    b = CorpusBuilder(tools, cfg, world)
    n_fraud = int(round(cfg.n_flagged * cfg.fraud_share_of_flags))
    plan = [(FRAUD_TRIGGERS[i % len(FRAUD_TRIGGERS)], True) for i in range(n_fraud)]
    plan += [(INNOCENT_TRIGGERS[i % len(INNOCENT_TRIGGERS)], False) for i in range(cfg.n_flagged - n_fraud)]
    order = b.rng.permutation(len(plan))
    cases = [b.case(no + 1, *plan[i]) for no, i in enumerate(order)]
    store = ClaimStore([c.claim for c in cases] + b.support)
    return cases, store, b
