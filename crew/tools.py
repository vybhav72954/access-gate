"""Deterministic tools over precomputed reference data (docs/02-LLD.md §5).

No LLM, no network, no side effects. Network adequacy is arithmetic, so it
lives here rather than inside an agent (design decision DD-2).

Every table is read with keep_default_na=False: `NA` is a real specialty code
(Paediatric Cancer) that pandas otherwise turns into NaN (data defect D-5).
"""
from __future__ import annotations

import math
from functools import cached_property
from pathlib import Path

import pandas as pd

from crew.schemas import Adequacy, Hospital, Package

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data" / "reference"

# Punjab, Gujarat and Telangana bulk-empanel basic facilities against broad
# package lists -- together ~95% of CHC/PHC tertiary listings. Treating that as
# a capability signal would flag three states' data conventions, not
# misconduct (data defect D-3, risk R-04).
BULK_STATES = frozenset({"PUNJAB", "GUJARAT", "TELANGANA"})

EARTH_R_KM = 6371.0


def _csv(name: str) -> pd.DataFrame:
    path = REF / name
    if not path.exists():
        raise FileNotFoundError(f"data/reference/{name} is missing: rebuild the reference tables (README, "
                                f"'Quickstart': scripts/build_reference.py, build_lookups.py, build_packages.py)")
    return pd.read_csv(path, keep_default_na=False)


class Tools:
    """Loads each reference table once; every method is a pure lookup."""

    @cached_property
    def _code_to_specialty(self) -> dict[str, str]:
        df = _csv("specialty_canonical.csv")
        return {code: row.specialty for row in df.itertuples() for code in row.registry_codes.split("|")}

    @cached_property
    def specialty_names(self) -> dict[str, str]:
        df = _csv("specialty_canonical.csv")
        return dict(zip(df.specialty, df.name))

    @cached_property
    def tertiary(self) -> frozenset[str]:
        df = _csv("specialty_canonical.csv")
        return frozenset(df.loc[df.tertiary.astype(str).str.lower() == "true", "specialty"])

    @cached_property
    def _registry(self) -> dict[str, Hospital]:
        df = _csv("registry_pseudonymised.csv")
        return {
            r.hospital_ref: Hospital(
                hospital_ref=r.hospital_ref, district_code=int(r.district_code), state=r.state,
                hospital_type=r.hospital_type, basic_tier=str(r.basic_tier).lower() == "true",
                specialties=[s for s in r.specialties.split("|") if s])
            for r in df.itertuples()
        }

    @cached_property
    def _packages(self) -> dict[str, Package]:
        rates = _csv("hbp_package_rates.csv")
        daycare = set(_csv("daycare_candidates.csv").package_code)
        # Every listing, and each listing's reserved flag: 473 packages sit under several specialties (F-35).
        listed: dict[str, dict[str, bool]] = {}
        referral: set[str] = set()
        for r in _csv("hbp_package_listings.csv").itertuples():
            spec = self._code_to_specialty.get(r.specialty_code)
            if spec:
                listed.setdefault(r.package_code, {})[spec] = str(r.govt_reserved).strip().lower() == "yes"
            if str(r.referral_basis).strip().lower() == "yes":
                referral.add(r.package_code)
        out = {}
        for r in rates.itertuples():
            amount = pd.to_numeric(r.package_amount_rs, errors="coerce")
            under = listed.get(r.package_code) or {}
            # The package's own specialty is the one its code names (MP001C: MP), not whichever file sorted first.
            own = self._code_to_specialty.get(r.package_code[:2]) or self._code_to_specialty.get(r.specialty_code)
            specialties = sorted(under, key=lambda s: s != own)
            out[r.package_code] = Package(
                package_code=r.package_code, package_name=str(r.package_name).strip(), specialty=own,
                amount_rs=0 if pd.isna(amount) else int(amount),
                pre_investigations=str(r.pre_investigations), post_investigations=str(r.post_investigations),
                govt_reserved=all(under.values()) if under else str(r.govt_reserved).strip().lower() == "yes",
                daycare_candidate=r.package_code in daycare, specialties=specialties, reserved_under=under,
                referral_allowed=r.package_code in referral)
        return out

    @cached_property
    def _cells(self) -> dict[tuple[int, str], int]:
        df = _csv("district_adequacy.csv")
        return {(int(r.district_code), r.specialty): int(r.n_providers) for r in df.itertuples()}

    @cached_property
    def _access(self) -> dict[tuple[int, str], tuple[float, str]]:
        df = _csv("access_distance.csv")
        return {(int(r.district_code), r.specialty): (float(r.km_to_alternative), r.nearest_alternative)
                for r in df.itertuples()}

    @cached_property
    def _points(self) -> pd.DataFrame:
        df = _csv("district_points.csv")
        df["district_code"] = df.district_code.astype(int)
        return df.set_index("district_code")

    @cached_property
    def _aspirational(self) -> frozenset[int]:
        asp = pd.read_csv(ROOT / "data/district/niti_aspirational_districts.csv", comment="#")
        return frozenset(pd.to_numeric(asp.district_code, errors="coerce").dropna().astype(int))

    # ── the five tools the agents rely on ─────────────────────────────────

    def registry_lookup(self, hospital_ref: str) -> Hospital | None:
        return self._registry.get(hospital_ref)

    def hbp_lookup(self, package_code: str) -> Package | None:
        return self._packages.get(package_code)

    def capability_flag(self, hospital: Hospital, specialty: str) -> tuple[bool, str, bool]:
        """(plausible, reason, state_convention). A FLAG, never a conclusion (EC-3)."""
        if not (hospital.basic_tier and specialty in self.tertiary):
            return True, "no facility-tier / specialty mismatch", False
        if hospital.state.upper() in BULK_STATES:
            return True, f"{hospital.state.title()} bulk-empanels basic facilities - a state convention, not a signal", True
        return False, f"basic-tier facility (CHC/PHC) listed for tertiary specialty '{specialty}'", False

    @cached_property
    def _network(self) -> pd.DataFrame:
        """District names and population for districts that have no boundary geometry (23 of them)."""
        df = _csv("district_network.csv")
        df["district_code"] = df.district_code.astype(int)
        return df.set_index("district_code")

    def district_adequacy(self, hospital: Hospital, specialty: str) -> Adequacy | None:
        """Provider counts need no geometry; only the distance to an alternative does. A district without a
        boundary still gets its adequacy, with no distance -- which the gate treats conservatively."""
        dc = hospital.district_code
        if dc in self._points.index:
            pt = self._points.loc[dc]
            district, state, pop = pt.district, pt.state, pt.pop_now
        elif dc in self._network.index:
            row = self._network.loc[dc]
            district, state, pop = row.district_name_english, row.state_name_english, row.pop_now
        else:
            return None
        n = self._cells.get((dc, specialty), 0)
        km, nearest = self._access.get((dc, specialty), (None, None))
        ok, reason, convention = self.capability_flag(hospital, specialty)
        return Adequacy(
            district_code=dc, district=district, state=state, specialty=specialty,
            specialty_name=self.specialty_names.get(specialty, specialty),
            n_providers=n, km_to_alternative=km, nearest_alternative=nearest,
            population=int(round(float(pop))) if str(pop) else 0,
            aspirational=dc in self._aspirational,
            capability_ok=ok, state_convention=convention, capability_reason=reason,
            hospital_listed=specialty in hospital.specialties, registry_blank=not hospital.specialties)

    def hospital_network(self, hospital: Hospital) -> list[Adequacy]:
        """Adequacy for every specialty the hospital is empanelled for: what a suspension would remove."""
        return [a for s in hospital.specialties if (a := self.district_adequacy(hospital, s)) is not None]

    # ── helpers ───────────────────────────────────────────────────────────

    @cached_property
    def _district_names(self) -> dict[int, str]:
        """Every district a claim can name: those with boundary geometry AND the 23 without (from the network)."""
        names = dict(zip(self._network.index, self._network.district_name_english))
        names.update(zip(self._points.index, self._points.district))
        return {int(c): str(n).strip().lower() for c, n in names.items()}

    def resolve_district(self, name: str) -> list[int]:
        key = name.strip().lower()
        return sorted(c for c, d in self._district_names.items() if d == key)

    def district_km(self, a: int, b: int) -> float | None:
        """Straight-line km between district representative points; None when either has no geometry."""
        if a not in self._points.index or b not in self._points.index:
            return None
        p, q = self._points.loc[a], self._points.loc[b]
        la1, lo1, la2, lo2 = map(math.radians, (float(p.lat), float(p.lon), float(q.lat), float(q.lon)))
        h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
        return 2 * EARTH_R_KM * math.asin(math.sqrt(h))

    def district_label(self, code: int) -> str:
        if code in self._points.index:
            pt = self._points.loc[code]
            return f"{pt.district}, {pt.state}"
        if code in self._network.index:
            row = self._network.loc[code]
            return f"{row.district_name_english}, {row.state_name_english}"
        return f"district {code}"

    def ambiguous_district_names(self) -> list[str]:
        counts = pd.Series(list(self._district_names.values())).value_counts()
        return sorted(counts[counts > 1].index)

    def hospitals(self) -> list[Hospital]:
        return list(self._registry.values())

    def packages(self) -> list[Package]:
        return list(self._packages.values())
