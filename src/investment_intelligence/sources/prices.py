"""Daily price bars: adapter interface and the Yahoo Finance implementation.

A deliberately separate shape from `sources/base.py`. `FilingSource` and
`ReportedFact` are built around one filing producing many line items with a
fiscal period and a basis (standalone/consolidated) -- none of that applies
to a daily close. Forcing prices through the filings Protocol would mean
inventing a fake `period_type` and `basis` for every bar, which is the kind
of "make the type fit" move that later reads as an adapter nobody understood,
not one nobody needed.

The discipline carries over even though the shape does not: validate at the
boundary (`PriceBar.__post_init__`), one adapter per provider behind one
interface, and `source_id` tied to a row in `sources` so an unlicensed
provider cannot be wired in.

The pattern here mirrors `paper-trader`'s `DataSource`/`Bar` deliberately --
same validation rules, same reasoning about a provider's malformed rows. It
is a second implementation of the idea, not an import of paper-trader's code:
[[paper-trader]] stays a separate, standalone project by design, and Phase 22
already anticipates consuming it as an actual library dependency later if
that turns out to be worth the coupling. For now this module exists on its
own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterator, Protocol, runtime_checkable

from investment_intelligence.sources.base import Rejection


class InvalidBar(ValueError):
    """A price bar that cannot be trusted into the store."""


@dataclass(frozen=True)
class PriceBar:
    """One daily OHLCV bar for one instrument.

    Values are `Decimal`, not `float`, for the same reason `ReportedFact.value`
    is: a float silently loses precision on money, and the loss does not show
    up anywhere until two numbers that should reconcile do not.
    """

    instrument_ref: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise InvalidBar(f"{self.instrument_ref} {self.day}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise InvalidBar(f"{self.instrument_ref} {self.day}: open {self.open} outside [{self.low}, {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise InvalidBar(f"{self.instrument_ref} {self.day}: close {self.close} outside [{self.low}, {self.high}]")
        if self.volume < 0:
            raise InvalidBar(f"{self.instrument_ref} {self.day}: negative volume {self.volume}")


@runtime_checkable
class PriceSource(Protocol):
    """What every price adapter must provide."""

    source_id: str
    key_scheme: str

    def fetch_bars(self, ref: str, start: date, end: date) -> Iterator[PriceBar | Rejection]:
        """Daily bars for one instrument in [start, end], inclusive.

        Yields rather than returns a list, matching `FilingSource.fetch_filings`
        -- a ten-year daily backfill is thousands of rows, and a Rejection lets
        one bad row be skipped and counted without losing the rest.
        """
        ...


class YFinancePriceSource:
    """Yahoo Finance daily bars for US-listed instruments.

    Coverage note, because it is the reason this adapter is safe to have at
    all: every instrument this project tracks is US-listed (NYSE/NASDAQ ADRs,
    or a direct listing for GLOB). Their price history is ordinary US equity
    market data under Yahoo's normal terms -- not NSE data. The ₹1.1L/yr NSE
    display tariff and clause 7.4's ban on simulation
    (docs/03-data-sources.md §1) govern Indian-exchange-listed prices, which
    this adapter does not fetch and is not licensed to fetch. If an Indian
    NSE/BSE-listed instrument is ever added to the tracked set, this adapter
    must not be pointed at it without redoing that research first.
    """

    source_id = "YAHOO_FINANCE"
    key_scheme = "US_TICKER"

    def fetch_bars(self, ref: str, start: date, end: date) -> Iterator[PriceBar | Rejection]:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "yfinance is not installed. Run: pip install -r requirements.txt"
            ) from exc

        # yfinance treats `end` as exclusive; we want inclusive, matching
        # every other as-of boundary in this codebase.
        frame = yf.download(
            ref,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            progress=False,
            auto_adjust=False,
        )

        if frame is None or frame.empty:
            yield Rejection(instrument_ref=ref, detail=f"no bars in [{start}, {end}]",
                             reason="NO_DATA")
            return

        # yfinance's column shape varies by version and by single- vs
        # multi-symbol request. Flatten rather than trust a fixed shape.
        if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
            frame.columns = frame.columns.get_level_values(0)

        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = required - set(frame.columns)
        if missing:
            yield Rejection(instrument_ref=ref, detail=f"missing columns {sorted(missing)}",
                             reason="MALFORMED_RESPONSE")
            return

        for index, row in frame.iterrows():
            values = [row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]]
            if any(v != v for v in values):  # NaN != NaN; a NaN row is unusable
                day = index.date() if hasattr(index, "date") else str(index)[:10]
                yield Rejection(instrument_ref=ref, detail=f"NaN in row for {day}",
                                 reason="INCOMPLETE_BAR")
                continue

            day = index.date() if hasattr(index, "date") else date.fromisoformat(str(index)[:10])
            try:
                yield PriceBar(
                    instrument_ref=ref,
                    day=day,
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    volume=int(row["Volume"]),
                )
            except InvalidBar as exc:
                yield Rejection(instrument_ref=ref, detail=str(exc), reason="INVALID_BAR")
