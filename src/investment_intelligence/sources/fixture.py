"""A deterministic in-memory source, for tests and for exercising the engine.

This is not a placeholder for a real adapter -- it is the thing that makes the
backfill engine testable at all. The behaviours that matter in Phase 7 are
resumption after interruption, idempotency on re-run, and gap enumeration, and
none of them can be tested reliably against a live provider: you cannot ask a
real API to fail on the fourth company, or to restate a figure on demand.

Architecture §2.2's adapter interface is what makes this possible. The engine
cannot tell this apart from a real source, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterator

from investment_intelligence.sources.base import FilingDocument, Rejection


@dataclass
class FixtureSource:
    """Serves pre-built documents per ISIN.

    `fail_on` lets a test make one company raise, so resumption and per-company
    isolation can be checked. `calls` records what was asked for, which is how
    a test asserts that a resumed run did NOT re-fetch completed work -- the
    single most important property of a resumable backfill.
    """

    source_id: str = "FIXTURE"
    key_scheme: str = "SEC_CIK"
    documents: dict[str, list[FilingDocument | Rejection]] = field(default_factory=dict)
    fail_on: set[str] = field(default_factory=set)
    on_fetch: Callable[[str], None] | None = None
    calls: list[str] = field(default_factory=list)

    def fetch_filings(
        self, ref: str, since: date, until: date
    ) -> Iterator[FilingDocument | Rejection]:
        self.calls.append(ref)

        if self.on_fetch is not None:
            self.on_fetch(ref)

        if ref in self.fail_on:
            raise ConnectionError(f"simulated provider failure for {ref}")

        if ref not in self.documents:
            # A reference this source has never heard of is a Rejection, not
            # silence. Found by the adapter contract test: EdgarSource already
            # behaved this way and FixtureSource yielded nothing, so the test
            # double had quietly diverged from the real adapter -- which means
            # the engine tests were validating behaviour production does not
            # have.
            #
            # The distinction the engine needs: a ref present here with an
            # empty list is a KNOWN company with nothing to load (SKIPPED); a
            # ref absent entirely is a configuration gap. Collapsing them is
            # the same mistake as collapsing DONE into SKIPPED.
            yield Rejection(
                instrument_ref=ref,
                detail="not in fixture",
                reason="unknown reference: this source has no record of it",
            )
            return

        for item in self.documents[ref]:
            if isinstance(item, Rejection):
                yield item
            elif since <= item.period_end <= until:
                yield item
