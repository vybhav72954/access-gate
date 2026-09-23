"""The figure inliner, against the file it rewrites.

`scripts/inline_figures.py` edits `ACCESS-GATE.html` in place, and that page is the project's single
source of truth. A bug here does not produce a wrong number somewhere — it corrupts the document
everything else is summarised from, and the corruption is 6 MB of base64 that nobody will read.

So the property that matters is the round trip: inlining and then extracting must give back exactly
the file we started with, byte for byte, including its line endings. Everything else here exists to
make sure the failure is loud — a missing figure, a changed figure, an `<img>` with no source.

These build their own tiny page in a tmp directory rather than touching the real one.
"""
import base64
import shutil
from pathlib import Path

import pytest

from scripts import inline_figures

ROOT = Path(__file__).resolve().parent.parent

# A minimal page, written the way the real one writes its images: src first, then the sizes and the
# alt text that must survive the trip.
PAGE = (
    "<!doctype html>\r\n"
    "<html><body>\r\n"
    '<figure><img src="figs/one.png" width="4" height="4" alt="the first"></figure>\r\n'
    "<p>Prose between them, which must not move.</p>\r\n"
    '<figure><img src="figs/two.jpg" width="6" height="2" alt="the second"></figure>\r\n'
    "</body></html>\r\n"
)

# A 1x1 PNG and a 1x1 JPEG: real bytes, so the base64 round trip is a real one.
ONE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
TWO_JPG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwc"
    "KDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAA"
    "AAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


@pytest.fixture
def page(tmp_path, monkeypatch):
    """A throwaway copy of the script pointed at a throwaway page."""
    figures = tmp_path / "figs"
    figures.mkdir()
    (figures / "one.png").write_bytes(ONE_PNG)
    (figures / "two.jpg").write_bytes(TWO_JPG)

    target = tmp_path / "PAGE.html"
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(PAGE)

    monkeypatch.setattr(inline_figures, "ROOT", tmp_path)
    monkeypatch.setattr(inline_figures, "PAGE", target)
    return target


def read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def test_inlining_embeds_every_figure(page):
    assert inline_figures.main([]) == 0
    html = read(page)
    assert html.count('src="data:image/png;base64,') == 1
    assert html.count('src="data:image/jpeg;base64,') == 1
    # The leading space matters: `data-src` keeps the path on purpose, and must not match here.
    assert ' src="figs/' not in html


def test_the_embedded_bytes_are_the_file(page):
    inline_figures.main([])
    html = read(page)
    encoded = html.split('src="data:image/png;base64,')[1].split('"')[0]
    assert base64.b64decode(encoded) == ONE_PNG


def test_extract_restores_the_original_byte_for_byte(page):
    """The property the whole script rests on, line endings included."""
    before = read(page)
    inline_figures.main([])
    assert read(page) != before
    inline_figures.main(["--extract"])
    assert read(page) == before


def test_inlining_twice_changes_nothing(page):
    inline_figures.main([])
    once = read(page)
    inline_figures.main([])
    assert read(page) == once


def test_the_width_height_and_alt_survive(page):
    inline_figures.main([])
    html = read(page)
    for attribute in ('width="4"', 'height="4"', 'alt="the first"', 'alt="the second"'):
        assert attribute in html


def test_each_image_records_where_it_came_from(page):
    """Without `data-src` the operation is one-way, and nothing could re-inline or verify."""
    inline_figures.main([])
    html = read(page)
    assert 'data-src="figs/one.png"' in html
    assert 'data-src="figs/two.jpg"' in html


def test_check_passes_when_the_page_matches_its_figures(page):
    inline_figures.main([])
    assert inline_figures.main(["--check"]) == 0


def test_check_fails_when_a_figure_has_changed(page, tmp_path):
    inline_figures.main([])
    shutil.copyfile(tmp_path / "figs" / "two.jpg", tmp_path / "figs" / "one.png")
    assert inline_figures.main(["--check"]) == 1


def test_check_leaves_the_page_alone(page, tmp_path):
    """A check that rewrote the file would make `--check` in CI a mutation."""
    inline_figures.main([])
    inlined = read(page)
    shutil.copyfile(tmp_path / "figs" / "two.jpg", tmp_path / "figs" / "one.png")
    inline_figures.main(["--check"])
    assert read(page) == inlined


def test_a_missing_figure_fails_instead_of_emitting_a_broken_page(page, tmp_path):
    (tmp_path / "figs" / "one.png").unlink()
    before = read(page)
    assert inline_figures.main([]) == 1
    assert read(page) == before


def test_an_unknown_image_type_is_refused(page, tmp_path):
    (tmp_path / "figs" / "three.bmp").write_bytes(b"BM")
    with open(page, "a", encoding="utf-8", newline="") as handle:
        handle.write('<img src="figs/three.bmp" alt="odd">\r\n')
    assert inline_figures.main([]) == 1


def test_the_committed_page_is_not_stale():
    """The real one: its figures must be the figures on disk."""
    if not (ROOT / "ACCESS-GATE.html").exists():
        pytest.skip("ACCESS-GATE.html is not in this checkout")
    assert inline_figures.main(["--check"]) == 0
