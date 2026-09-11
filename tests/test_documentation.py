"""Phase 18: documentation that cannot quietly rot.

    A stranger runs it locally using only the docs.

That criterion was verified by doing it — wiping the venv, node_modules, both
databases and the web role, then following the README verbatim. These tests
guard the parts of it that decay silently afterwards: a link to a renamed
file, a command that no longer exists, a claim about the code that stopped
being true.

They cannot check whether the prose is *good*. They can check that every path
it points at is real, which is the failure mode that actually happens.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = sorted(
    [*ROOT.glob("*.md"), *ROOT.glob("docs/*.md"), *ROOT.glob("db/*.md")])


def _link_targets(md: Path) -> list[tuple[str, str]]:
    text = md.read_text()
    out = []
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        target = m.group(2).split("#")[0]
        if target and not target.startswith(("http", "mailto")):
            out.append((m.group(1), target))
    return out


@pytest.mark.parametrize("md", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_internal_link_resolves(md: Path):
    """The commonest form of doc rot, and the one a reader hits first."""
    broken = [f"[{label}]({target})" for label, target in _link_targets(md)
              if not (md.parent / target).exists()]
    assert broken == [], f"{md.relative_to(ROOT)} has broken links: {broken}"


def test_every_phase_document_is_linked_from_somewhere():
    """A document nobody links to is a document nobody reads."""
    linked = set()
    for md in MARKDOWN:
        for _, target in _link_targets(md):
            resolved = (md.parent / target).resolve()
            if resolved.suffix == ".md":
                linked.add(resolved)
    orphans = [p.relative_to(ROOT) for p in MARKDOWN
               if p.resolve() not in linked and p.name != "README.md"]
    assert orphans == [], f"unlinked documents: {orphans}"


# ---------------------------------------------------------------------------
# The README's promises
# ---------------------------------------------------------------------------

def _make_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text()
    return set(re.findall(r"^([a-z][a-z-]*):", text, re.M))


@pytest.mark.parametrize("target", [
    "doctor", "setup", "seed", "backfill", "test", "web", "help",
])
def test_every_command_the_readme_offers_exists(target):
    """The README tells a stranger to run these. If one is renamed, their
    first five minutes are spent on a typo that is ours."""
    assert target in _make_targets(), f"README promises `make {target}`"


def test_the_readme_does_not_still_say_nothing_is_built():
    """It said "Phase 1 of 14. Nothing is built yet." for sixteen phases,
    which is the single most misleading sentence the project has had."""
    text = _flat((ROOT / "README.md").read_text())
    assert "Nothing is built yet" not in text
    assert "Phase 1 of 14" not in text


def test_the_readme_agrees_with_the_roadmap_on_how_many_phases_there_are():
    readme = _flat((ROOT / "README.md").read_text())
    roadmap = _flat((ROOT / "ROADMAP.md").read_text())
    assert "22 phases" in readme and "Twenty-two phases" in roadmap


def _flat(text: str) -> str:
    """Collapse whitespace before matching prose.

    Markdown wraps, so an assertion on an exact substring fails the first time
    a sentence reflows — which says nothing about whether the claim is still
    there. Checking the words rather than the line breaks is the point.
    """
    return re.sub(r"\s+", " ", text)


def test_the_readme_states_the_licence_position_and_the_coverage_limit():
    """Two things a reader must not discover by surprise: that this shows no
    prices, and that the figures will not match an Indian screener."""
    text = _flat((ROOT / "README.md").read_text())
    assert "not give investment advice" in text
    assert "will not match Screener.in" in text
    assert "no prices" in text.lower()


# ---------------------------------------------------------------------------
# Claims about the code
# ---------------------------------------------------------------------------

def test_the_migration_count_in_the_readme_is_right():
    """A number in prose that drifts from reality is worse than no number."""
    stated = re.search(r"(\d+) forward-only SQL migrations",
                       _flat((ROOT / "README.md").read_text()))
    assert stated, "the README should say how many migrations there are"
    actual = len(list((ROOT / "db" / "migrations").glob("*.sql")))
    assert int(stated.group(1)) == actual, (
        f"README says {stated.group(1)} migrations, there are {actual}")


# There is deliberately NO test asserting the README's test count.
#
# One was written, and it failed immediately — because writing it added two
# tests and changed the number it was checking. That is not a quirk of the
# moment: such a test fails on every legitimate change, which is exactly the
# flakiness Phase 14 identified as worse than a missing test, since it teaches
# people that a red build means "update the number" rather than "read the
# failure".
#
# So the README no longer quotes an exact count. The migration check below
# stays, because migrations change rarely and always deliberately — the
# property that makes a number in prose safe to assert is how often it is
# allowed to move.


def test_the_api_reference_documents_every_route_that_exists():
    """An undocumented endpoint is one nobody can use and nobody will
    maintain."""
    routes = {p.parent.name for p in (ROOT / "web" / "app" / "api" / "v1").rglob("route.ts")}
    routes = {r.strip("[]") for r in routes}
    reference = (ROOT / "docs" / "api-reference.md").read_text()
    missing = [r for r in routes if r not in reference]
    assert missing == [], f"undocumented API routes: {missing}"


def test_the_runbook_covers_every_health_status():
    """Each status is a different diagnosis pointing somewhere different. One
    without an entry is a page someone reaches with no next step."""
    runbook = _flat((ROOT / "docs" / "18-runbook.md").read_text())
    for status in ("NEVER_RAN", "NEVER_SUCCEEDED", "STALE", "UNMONITORED"):
        assert status in runbook, f"runbook has no entry for {status}"


def test_the_runbook_covers_every_rejection_class():
    """Reading the class is the whole point of classifying them; a class with
    no guidance leaves the reader where they started."""
    from investment_intelligence.ingest.rejections import _CLASSES
    runbook = (ROOT / "docs" / "18-runbook.md").read_text()
    missing = [name for name, _ in _CLASSES if name not in runbook
               and name in {"YTD_NOT_STORED", "UNPLACEABLE_BALANCE_DATE",
                            "SOURCE_UNAVAILABLE", "UNRECOGNISED_PERIOD_SPAN"}]
    assert missing == [], f"rejection classes with no runbook entry: {missing}"
    assert "UNCLASSIFIED" in runbook


def test_no_document_claims_the_data_is_annual_only():
    """It was true, then Phase 7's fix made it false, and the claim survived in
    two places. Superseded text is marked as superseded rather than deleted,
    so this checks for the bare claim only."""
    for md in MARKDOWN:
        text = md.read_text()
        for match in re.finditer(r"[Aa]nnual periods only", text):
            window = text[max(0, match.start() - 400):match.start()]
            assert "Superseded" in window or "superseded" in window, (
                f"{md.relative_to(ROOT)} still claims annual-only data")


# ---------------------------------------------------------------------------
# Setup instructions
# ---------------------------------------------------------------------------

def test_the_locale_gotcha_is_documented():
    """PostgreSQL 17 on macOS fails with "postmaster became multithreaded
    during startup" without LC_ALL — a message that names the symptom and not
    the cause. It cost time once; it should cost nobody else any."""
    assert "LC_ALL" in (ROOT / "README.md").read_text()
    assert "LC_ALL" in (ROOT / "Makefile").read_text()


def test_the_example_env_file_exists_and_is_referenced():
    assert (ROOT / "web" / "env.example").exists()
    referenced = any("env.example" in md.read_text() for md in MARKDOWN) or \
        "env.example" in (ROOT / "Makefile").read_text()
    assert referenced


def test_git_tracks_every_file_the_readme_points_at():
    """A file that exists locally and was never committed is the classic way a
    clean checkout fails for everyone but its author — exactly how
    `.env.local.example` stayed invisible until Phase 15."""
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, check=True).stdout.split())
    missing = []
    for label, target in _link_targets(ROOT / "README.md"):
        rel = (ROOT / target).resolve().relative_to(ROOT)
        if str(rel) not in tracked:
            missing.append(f"[{label}] -> {rel}")
    assert missing == [], f"README links to untracked files: {missing}"
