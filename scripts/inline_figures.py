"""Inline every figure into `ACCESS-GATE.html`, so the page is one standalone file.

The page is the project's single source of truth, and it gets sent to people: attached to an email,
dropped in a folder, opened from a USB stick, republished elsewhere. Referencing `docs/figures/`
relatively means it only renders correctly from inside the repository, and a reader who receives the
file alone sees twenty-two broken images with no way to know what was there.

So the bytes travel with the markup. Each `<img>` keeps the path it came from in `data-src`, which
is what makes this reversible and re-runnable: the inliner reads `data-src` when it is already
there, so a second run refreshes rather than double-encoding, and `--extract` puts the relative
paths back when someone wants a small file to edit or diff.

    python -m scripts.inline_figures            # inline every figure, in place
    python -m scripts.inline_figures --check    # fail if the page is stale against docs/figures/
    python -m scripts.inline_figures --extract  # put the relative paths back

The images are embedded as they are, byte for byte. Re-encoding them would save about 40%, but
these diagrams exist to be zoomed into — the text inside them is the content — and a page that is
2.6 MB smaller is not worth a page whose diagrams are worse. The screenshots are already JPEG.

Standard library only, like `scripts/export_frontend.py`: a teammate runs it in a bare checkout.
"""

from __future__ import annotations

import argparse
import base64
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "ACCESS-GATE.html"

MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}

# One <img> tag. `src` and the optional `data-src` may appear in either order, and the rest of the
# attributes (width, height, alt) are carried through untouched.
IMG = re.compile(r"<img\s(?P<attrs>[^>]*?)\s*/?>")
ATTR = re.compile(r'(?P<name>[\w-]+)="(?P<value>[^"]*)"')


class Stale(Exception):
    """The page does not match the figures on disk."""


def attributes(tag: str) -> dict[str, str]:
    return {m.group("name"): m.group("value") for m in ATTR.finditer(tag)}


def source_of(attrs: dict[str, str]) -> str:
    """Where this image came from: `data-src` once inlined, `src` before that."""
    origin = attrs.get("data-src") or attrs.get("src", "")
    if origin.startswith("data:"):
        raise Stale("an inlined image has no data-src recording where it came from")
    return origin


def data_uri(relative: str) -> str:
    path = ROOT / relative
    suffix = path.suffix.lower()
    if suffix not in MEDIA:
        raise Stale(f"{relative}: not an image type this page embeds")
    if not path.is_file():
        raise Stale(f"{relative}: no such file")
    return f"data:{MEDIA[suffix]};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def rewrite(html: str, *, inline: bool) -> tuple[str, int, int]:
    """Return the page with every image inlined (or extracted), how many, and the bytes embedded."""
    count = 0
    embedded = 0

    def one(match: re.Match[str]) -> str:
        nonlocal count, embedded
        attrs = attributes(match.group("attrs"))
        origin = source_of(attrs)
        if not origin:
            raise Stale("an <img> has neither src nor data-src")

        count += 1
        if inline:
            uri = data_uri(origin)
            embedded += len(uri)
            attrs = {"data-src": origin, "src": uri, **without(attrs, "data-src", "src")}
        else:
            attrs = {"src": origin, **without(attrs, "data-src", "src")}

        rendered = " ".join(f'{name}="{value}"' for name, value in attrs.items())
        return f"<img {rendered}>"

    return IMG.sub(one, html), count, embedded


def without(attrs: dict[str, str], *names: str) -> dict[str, str]:
    return {k: v for k, v in attrs.items() if k not in names}


def read() -> str:
    # newline="" so the file's own line endings survive a round trip untouched.
    with open(PAGE, encoding="utf-8", newline="") as handle:
        return handle.read()


def write(html: str) -> None:
    with open(PAGE, "w", encoding="utf-8", newline="") as handle:
        handle.write(html)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail if the page is stale")
    group.add_argument("--extract", action="store_true", help="put the relative paths back")
    args = parser.parse_args(argv)

    if not PAGE.exists():
        print(f"FAIL {PAGE} does not exist")
        return 1

    html = read()
    try:
        rebuilt, count, embedded = rewrite(html, inline=not args.extract)
    except Stale as problem:
        print(f"FAIL {problem}")
        return 1

    if args.check:
        same = rebuilt == html
        print(
            ("OK   " if same else "FAIL ")
            # ASCII only: this prints to a Windows console, which is cp1252.
            + f"{PAGE.name}: {count} figures "
            + ("match docs/figures/" if same else "are STALE - rerun without --check")
        )
        return 0 if same else 1

    write(rebuilt)
    size = len(rebuilt.encode("utf-8"))
    if args.extract:
        print(f"wrote {PAGE.name}  {size / 1024:.0f} KB, {count} figures now referenced from docs/figures/")
    else:
        print(
            f"wrote {PAGE.name}  {size / 1_000_000:.2f} MB, {count} figures inlined "
            f"({embedded / 1_000_000:.2f} MB of it). The file is standalone."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
