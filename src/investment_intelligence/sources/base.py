"""Source adapter interface and domain objects.

Architecture §2.2: one adapter per external provider, all behind a single
interface, and an adapter's only job is to turn "that provider's response"
into "our domain objects, or a rejection". When a provider changes its
response shape, exactly one file breaks.

The domain objects validate themselves on construction. That is the boundary
in "boundary validation" (§2.3) -- an invalid fact cannot exist as an object,
so it cannot reach the store by any path. Rejections are recorded and counted,
never imputed: inventing a revenue figure is worse than having a gap, because
the gap is visible and the invention is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Iterator, Protocol, runtime_checkable

PERIOD_TYPES = frozenset({"Q1", "Q2", "Q3", "Q4", "H1", "H2", "ANNUAL"})
BASES = frozenset({"STANDALONE", "CONSOLIDATED"})


class InvalidFact(ValueError):
    """A fact that cannot be trusted into the store."""


@dataclass(frozen=True)
class ReportedFact:
    """One line item, as reported in one filing.

    Deliberately carries no `known_from`. That is transaction time -- when WE
    learned it -- and it belongs to the ingestion run, not to the provider. An
    adapter that could set it could forge history.
    """

    line_item: str
    value: Decimal
    basis: str
    fiscal_year: int
    period_type: str
    period_start: date
    period_end: date
    currency: str = "INR"

    # A balance is an instant, not a span: total assets "as at 31 March" is not
    # a quantity that accumulated over the year the way revenue did. The
    # distinction already exists in the schema as `line_items.is_flow`, and it
    # had to exist here too the moment real EDGAR data arrived -- XBRL gives
    # balance-sheet facts no start date at all. Without this, every
    # balance-sheet item was being silently dropped, which would have quietly
    # removed leverage from the metric set.
    is_instant: bool = False

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise InvalidFact(f"basis must be one of {sorted(BASES)}, got {self.basis!r}")
        if self.period_type not in PERIOD_TYPES:
            raise InvalidFact(
                f"period_type must be one of {sorted(PERIOD_TYPES)}, "
                f"got {self.period_type!r}"
            )
        if not 1990 <= self.fiscal_year <= 2100:
            raise InvalidFact(f"implausible fiscal_year: {self.fiscal_year}")
        if self.period_end < self.period_start:
            raise InvalidFact(
                f"period ends before it starts: {self.period_start} to {self.period_end}"
            )
        if not isinstance(self.value, Decimal):
            raise InvalidFact(
                f"value must be Decimal, got {type(self.value).__name__}. "
                "Floats lose precision on money and the loss is invisible."
            )
        if self.value != self.value:  # NaN
            raise InvalidFact("value is NaN")
        if len(self.currency) != 3:
            raise InvalidFact(f"currency must be a 3-letter code, got {self.currency!r}")

        span_days = (self.period_end - self.period_start).days

        if self.is_instant:
            # An instant carries the date it was measured, twice. Attaching it
            # to a period (fiscal_year + period_type) is what lets a reader ask
            # for "FY2019 total assets" and get the balance as at the FY2019
            # year end, which is where they expect to find it.
            if span_days != 0:
                raise InvalidFact(
                    f"an instant must have period_start == period_end, "
                    f"got a {span_days}-day span"
                )
            return

        # A period label that disagrees with its own dates means the adapter
        # mapped something wrongly, and every downstream metric inherits it.
        expected = {
            "Q1": (80, 100), "Q2": (80, 100), "Q3": (80, 100), "Q4": (80, 100),
            "H1": (170, 195), "H2": (170, 195),
            "ANNUAL": (350, 400),
        }[self.period_type]
        if not expected[0] <= span_days <= expected[1]:
            raise InvalidFact(
                f"{self.period_type} should span {expected[0]}-{expected[1]} days, "
                f"got {span_days} ({self.period_start} to {self.period_end})"
            )


@dataclass(frozen=True)
class FilingDocument:
    """A filing, and the facts read out of it.

    `content_hash` lets ingestion recognise a document it has already parsed.
    `source_ref` is where it came from, so provenance survives to the UI.
    """

    instrument_ref: str          # value of the source's key_scheme identifier
    filing_type: str
    period_end: date
    filed_at: datetime
    source_ref: str
    content_hash: str
    facts: tuple[ReportedFact, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.instrument_ref:
            raise InvalidFact("instrument_ref is required")
        if not self.source_ref:
            raise InvalidFact("source_ref is required: a fact with no provenance is unusable")
        if not self.content_hash:
            raise InvalidFact("content_hash is required to detect re-parsed documents")
        if self.filed_at.tzinfo is None:
            raise InvalidFact("filed_at must be timezone-aware")


@dataclass(frozen=True)
class Rejection:
    """Something the source produced that we refused, and why.

    Counted in the run log. A run that rejects 80% of what it fetched
    succeeded technically and failed in every way that matters, and the only
    way to notice is to count.
    """

    instrument_ref: str
    detail: str
    reason: str


@runtime_checkable
class FilingSource(Protocol):
    """What every fundamentals adapter must provide.

    `source_id` must match a row in the `sources` table, which cannot exist
    without a verified licence note (001_sources.sql). So an adapter whose
    terms nobody has read cannot be wired in: the foreign key refuses its
    facts.
    """

    source_id: str

    # Which identifier scheme this source speaks. EDGAR knows companies by
    # SEC_CIK; an Indian filings adapter would use ISIN or NSE_SYMBOL. The
    # engine looks the instrument up in `instrument_external_ids` under this
    # scheme, so no adapter needs database access and no adapter has to care
    # what the others use.
    key_scheme: str

    def fetch_filings(
        self, ref: str, since: date, until: date
    ) -> Iterator[FilingDocument | Rejection]:
        """Filings for one instrument in a window.

        Yields rather than returns a list: a ten-year backfill for one company
        can be dozens of documents, and the caller checkpoints as it goes.
        Yielding `Rejection` rather than raising lets one bad document be
        skipped and counted without abandoning the rest of the company.
        """
        ...
