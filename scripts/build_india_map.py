"""Build the bundled India map the evidence viewer draws.

The viewer must render with the network off, so it cannot reach a tile server and cannot pull a
projection library at runtime. This script does all of that work once, offline, and writes plain SVG
path data: `frontend/src/lib/india-map.json`. The component that draws it only has to place strings.

Provenance, because the project's rule is that every figure traces to a published source:

  * Boundaries come from **DataMeet's** `maps` repository (MIT licence), pinned to one commit. Its
    state and district layers are digitised from the Survey of India state map and the 2011 Census
    district boundaries respectively.
  * That source is used **because of** its boundary depiction. It shows the full extent India
    claims — Gilgit-Baltistan to 37.08 N, Aksai Chin to 80.33 E — which is the depiction an Indian
    submission requires. Natural Earth and most Western sources draw the line of control instead,
    and are wrong for this purpose.
  * The district layer is 2011 vintage, so Jammu & Kashmir is one state and Telangana's districts
    still sit under Andhra Pradesh. The viewer says so on the map.

The sources are ~56 MB, far too large to commit, so they are downloaded once into a gitignored cache
and checked against recorded hashes. The build is deterministic: same sources and same parameters
give a byte-identical output file, which is the same contract `scripts/build_*.py` already keep.

    python -m scripts.build_india_map              # download if needed, then build
    python -m scripts.build_india_map --check      # rebuild and fail if the output would change

Standard library only, like `scripts/export_frontend.py`: a teammate runs it in a bare checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".mapcache"
OUT = ROOT / "frontend" / "src" / "lib" / "india-map.json"

# DataMeet `maps`, pinned. The repository's last commit is from 2022; the pin is what makes the
# rebuild reproducible rather than a promise that the branch will not move.
COMMIT = "b3fbbde595310b397a55d718e0958ce249a4fa1f"
RAW = f"https://raw.githubusercontent.com/datameet/maps/{COMMIT}"

SOURCES = {
    "states": (
        f"{RAW}/docs/data/geojson/states.geojson",
        "efef67f6d4bf892b3a46090f18861ce20330f3378428197225a43310ff64377f",
    ),
    "districts": (
        f"{RAW}/docs/data/geojson/dists11.geojson",
        "cafa345611b7d7a4d9923db57dba3d93afca4848c9e39f9b0be7f47a89dd78ad",
    ),
    "outline": (
        f"{RAW}/Country/india-soi.geojson",
        "e5321e2010d060c0bffa3b51a1e691af8fbf6063b848c02c123d61d4084cf820",
    ),
}

LICENCE = "MIT — DataMeet (github.com/datameet/maps), Survey of India + Census 2011 boundaries"

# The source spells some names its own way. The viewer shows the conventional form; the left-hand
# side is what the GeoJSON carries.
STATE_NAMES = {
    "Arunanchal Pradesh": "Arunachal Pradesh",
    "Andaman & Nicobar Island": "Andaman & Nicobar Islands",
    "NCT of Delhi": "Delhi",
    "Dadara & Nagar Havelli": "Dadra & Nagar Haveli",
}

# Districts the viewer can point at: the districts the ten decided cases sit in, and the districts
# their nearest alternative provider sits in. Keyed by the name the project uses; the value is the
# 2011 Census spelling, which differs for Ahmedabad. `tests/test_india_map.py` fails if a district
# named in cases.json is missing here, so the two cannot drift apart.
DISTRICTS = {
    ("Ahmedabad", "Gujarat"): ("Ahmadabad", "Gujarat"),
    ("Bahraich", "Uttar Pradesh"): ("Bahraich", "Uttar Pradesh"),
    ("Barwani", "Madhya Pradesh"): ("Barwani", "Madhya Pradesh"),
    ("Gonda", "Uttar Pradesh"): ("Gonda", "Uttar Pradesh"),
    ("Nandurbar", "Maharashtra"): ("Nandurbar", "Maharashtra"),
    ("Dhule", "Maharashtra"): ("Dhule", "Maharashtra"),
}

# ── projection ────────────────────────────────────────────────────────────────
# Albers equal-area conic. India spans 8 N to 37 N, so Mercator would inflate the Himalaya badly
# and a plate carree would shear the whole country. Equal-area is also the honest choice for a
# choropleth: a reader compares filled areas, so the areas should be comparable.
PHI_1, PHI_2 = math.radians(12.5), math.radians(32.5)  # standard parallels
PHI_0, LAMBDA_0 = math.radians(22.0), math.radians(80.0)  # origin

_N = (math.sin(PHI_1) + math.sin(PHI_2)) / 2
_C = math.cos(PHI_1) ** 2 + 2 * _N * math.sin(PHI_1)
_RHO_0 = math.sqrt(_C - 2 * _N * math.sin(PHI_0)) / _N

WIDTH = 900.0  # output canvas width; height follows from the projected aspect
TOLERANCE = 0.45  # Douglas-Peucker tolerance, in output units
MIN_AREA = 0.6  # drop rings smaller than this many square output units
PRECISION = 1  # decimal places kept in the path data


def project(lon: float, lat: float) -> tuple[float, float]:
    """Longitude/latitude in degrees to Albers equal-area conic, in radians-scaled units."""
    rho = math.sqrt(_C - 2 * _N * math.sin(math.radians(lat))) / _N
    theta = _N * (math.radians(lon) - LAMBDA_0)
    return rho * math.sin(theta), _RHO_0 - rho * math.cos(theta)


# ── geometry helpers ──────────────────────────────────────────────────────────


def rings(geometry: dict) -> list[list[list[float]]]:
    """Every linear ring of a Polygon or MultiPolygon, outer and inner alike."""
    kind = geometry["type"]
    if kind == "Polygon":
        return list(geometry["coordinates"])
    if kind == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    raise ValueError(f"unsupported geometry: {kind}")


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative so a long coastline cannot blow the recursion limit."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        span = math.hypot(dx, dy)
        worst, worst_at = -1.0, -1
        for i in range(start + 1, end):
            px, py = points[i]
            if span == 0:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * px - dx * py + bx * ay - by * ax) / span
            if distance > worst:
                worst, worst_at = distance, i
        if worst > tolerance:
            keep[worst_at] = True
            stack.append((start, worst_at))
            stack.append((worst_at, end))
    return [p for p, k in zip(points, keep) if k]


def signed_area(points: list[tuple[float, float]]) -> float:
    """Shoelace. The sign tells an outer ring from a hole; the magnitude is the area."""
    total = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total / 2


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Area-weighted centroid of one ring, which sits better than the mean of the vertices."""
    area = signed_area(points)
    if abs(area) < 1e-12:
        n = len(points) or 1
        return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
    cx = cy = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return cx / (6 * area), cy / (6 * area)


def to_path(shapes: list[list[tuple[float, float]]]) -> str:
    """One SVG path covering every ring, `fill-rule: evenodd` letting the holes punch through."""
    parts = []
    for ring in shapes:
        if len(ring) < 3:
            continue
        head = f"M{round(ring[0][0], PRECISION)} {round(ring[0][1], PRECISION)}"
        tail = "".join(f"L{round(x, PRECISION)} {round(y, PRECISION)}" for x, y in ring[1:])
        parts.append(head + tail + "Z")
    return "".join(parts)


def bounds(shapes: list[list[tuple[float, float]]]) -> list[float]:
    """[x0, y0, x1, y1] over every kept ring, so a page can crop the viewBox to one region."""
    xs = [x for ring in shapes for x, _ in ring]
    ys = [y for ring in shapes for _, y in ring]
    return [
        round(min(xs), PRECISION),
        round(min(ys), PRECISION),
        round(max(xs), PRECISION),
        round(max(ys), PRECISION),
    ]


# ── the build ─────────────────────────────────────────────────────────────────


def fetch(name: str, offline: bool) -> dict:
    url, want = SOURCES[name]
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / f"{name}.geojson"
    if not cached.exists():
        if offline:
            raise SystemExit(f"{cached} is missing and --offline was given. Drop --offline to fetch it.")
        print(f"  downloading {name} ...", flush=True)
        with urllib.request.urlopen(url, timeout=180) as response:  # noqa: S310 - pinned https URL
            cached.write_bytes(response.read())
    got = hashlib.sha256(cached.read_bytes()).hexdigest()
    if got != want:
        raise SystemExit(
            f"{cached} does not match the recorded hash.\n  expected {want}\n  got      {got}\n"
            "Delete the file to re-download, or update SOURCES if the pin moved on purpose."
        )
    return json.loads(cached.read_text(encoding="utf-8"))


def prepare(features: list[tuple[str, str, dict]]) -> tuple[dict, float, float, float, float]:
    """Project every ring once, and return them with the bounding box they occupy."""
    projected: dict[tuple[str, str], list[list[tuple[float, float]]]] = {}
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for name, state, geometry in features:
        shapes = []
        for ring in rings(geometry):
            points = [project(lon, lat) for lon, lat, *_ in ring]
            if points and points[0] == points[-1]:
                points.pop()
            if len(points) < 3:
                continue
            shapes.append(points)
            for x, y in points:
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
        projected[(name, state)] = shapes
    return projected, min_x, min_y, max_x, max_y


def build(offline: bool) -> str:
    print("reading sources")
    states_geo = fetch("states", offline)
    districts_geo = fetch("districts", offline)
    outline_geo = fetch("outline", offline)

    wanted = {census: project_name for project_name, census in DISTRICTS.items()}

    features: list[tuple[str, str, dict]] = []
    for feature in states_geo["features"]:
        raw = feature["properties"]["ST_NM"]
        features.append((STATE_NAMES.get(raw, raw), "@state", feature["geometry"]))
    for feature in districts_geo["features"]:
        key = (feature["properties"]["DISTRICT"], feature["properties"]["ST_NM"])
        if key in wanted:
            name, state = wanted[key]
            features.append((name, state, feature["geometry"]))
    for feature in outline_geo["features"]:
        features.append(("India", "@outline", feature["geometry"]))

    missing = set(DISTRICTS) - {(n, s) for n, s, _ in features}
    if missing:
        raise SystemExit(f"districts not found in the source: {sorted(missing)}")

    print(f"projecting {len(features)} features")
    projected, min_x, min_y, max_x, max_y = prepare(features)

    # Fit the projected country to the canvas. Both axes take the same scale, so nothing is
    # stretched, and y is flipped because SVG counts downwards.
    scale = WIDTH / (max_x - min_x)
    height = (max_y - min_y) * scale

    def place(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [((x - min_x) * scale, height - (y - min_y) * scale) for x, y in points]

    states: list[dict] = []
    districts: list[dict] = []
    outline = ""
    for (name, state), shapes in projected.items():
        placed = [place(ring) for ring in shapes]
        placed.sort(key=lambda ring: abs(signed_area(ring)), reverse=True)
        kept = [ring for ring in placed if abs(signed_area(ring)) >= MIN_AREA] or placed[:1]
        simplified = [simplify(ring, TOLERANCE) for ring in kept]
        simplified = [ring for ring in simplified if len(ring) >= 3]
        path = to_path(simplified)
        anchor_x, anchor_y = centroid(max(simplified, key=lambda r: abs(signed_area(r))))
        entry = {
            "name": name,
            "d": path,
            "cx": round(anchor_x, PRECISION),
            "cy": round(anchor_y, PRECISION),
            "bbox": bounds(simplified),
        }
        if state == "@outline":
            outline = path
        elif state == "@state":
            states.append(entry)
        else:
            districts.append({**entry, "state": state})

    states.sort(key=lambda s: s["name"])
    districts.sort(key=lambda d: (d["state"], d["name"]))

    document = {
        "_source": {
            "repository": "github.com/datameet/maps",
            "commit": COMMIT,
            "licence": LICENCE,
            "boundaries": (
                "Survey of India state boundaries and 2011 Census district boundaries. Shows the "
                "full extent India claims. District vintage is 2011: Jammu & Kashmir is one state "
                "and Telangana's districts are recorded under Andhra Pradesh."
            ),
            "generated_by": "scripts/build_india_map.py",
        },
        "projection": {
            "name": "Albers equal-area conic",
            "standard_parallels": [12.5, 32.5],
            "origin": [80.0, 22.0],
            "tolerance": TOLERANCE,
        },
        "width": round(WIDTH, PRECISION),
        "height": round(height, PRECISION),
        "outline": outline,
        "states": states,
        "districts": districts,
    }
    # `separators` and a trailing newline keep the file stable across runs and platforms.
    return json.dumps(document, indent="\t", ensure_ascii=False, separators=(",", ": ")) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file would change")
    parser.add_argument("--offline", action="store_true", help="use the cache only, never download")
    args = parser.parse_args(argv)

    text = build(args.offline)

    if args.check:
        if not OUT.exists():
            print(f"FAIL {OUT} does not exist")
            return 1
        same = OUT.read_text(encoding="utf-8") == text
        print(("OK   " if same else "FAIL ") + f"{OUT.relative_to(ROOT)} is "
              + ("byte-identical" if same else "STALE — rerun without --check"))
        return 0 if same else 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    document = json.loads(text)
    print(
        f"wrote {OUT.relative_to(ROOT)}  "
        f"{len(text) / 1024:.0f} KB, {len(document['states'])} states, "
        f"{len(document['districts'])} districts, canvas "
        f"{document['width']}x{document['height']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
