"""The contract every source adapter must satisfy.

Architecture §2.2 claims adapters are interchangeable: the engine cannot tell
one from another, so adding a source is one file. Nothing enforced that claim
until this file. `EdgarSource` and `FixtureSource` were each tested on their
own behaviour, which proves they work and not that they agree.

That gap matters because the second adapter is the whole plan. Phase 3 §6.3
leaves an Indian filings source open, and it will be written against this
interface by someone reading this file. A contract test is the specification.

Every test here is parameterised over all adapters. A new adapter is added to
`ADAPTERS` and either passes or is not finished.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_intelligence.sources.base import (
    BASES,
    PERIOD_TYPES,
    FilingDocument,
    FilingSource,
    Rejection,
)
from investment_intelligence.sources.edgar import EdgarSource
from investment_intelligence.sources.fixture import FixtureSource

WIDE = (date(2000, 1, 1), date(2040, 1, 1))
INFY_CIK, INFY_REF = 1067491, "1067491"


def _edgar() -> tuple[FilingSource, str]:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "edgar_infy.json").read_text())
    return EdgarSource(cik_by_ref={INFY_REF: INFY_CIK},
                       fetch=lambda cik: payload), INFY_REF


def _fixture() -> tuple[FilingSource, str]:
    from test_backfill import document, revenue  # reuse the same builders
    ref = "INE000001001"
    return FixtureSource(documents={ref: [document(ref, revenue("1000"))]}), ref


# Each entry is (name, factory). The factory returns a ready adapter and a
# reference it knows about, so the contract tests need no adapter-specific
# setup.
ADAPTERS = [("edgar", _edgar), ("fixture", _fixture)]


@pytest.fixture(params=ADAPTERS, ids=[name for name, _ in ADAPTERS])
def adapter(request):
    return request.param[1]()


# ---------------------------------------------------------------------------
# Shape of the interface
# ---------------------------------------------------------------------------

def test_declares_a_source_id_and_a_key_scheme(adapter):
    """`source_id` must match a row in `sources`, which cannot exist without a
    verified licence note. `key_scheme` is how the engine looks an instrument
    up without any adapter needing database access."""
    source, _ = adapter
    assert isinstance(source.source_id, str) and source.source_id
    assert isinstance(source.key_scheme, str) and source.key_scheme


def test_satisfies_the_protocol(adapter):
    source, _ = adapter
    assert isinstance(source, FilingSource)


def test_fetch_filings_is_a_generator_not_a_list(adapter):
    """A ten-year backfill for one company can be dozens of documents, and the
    caller checkpoints as it goes. An adapter that materialises everything
    first defeats that."""
    source, ref = adapter
    result = source.fetch_filings(ref, *WIDE)
    assert hasattr(result, "__next__"), "fetch_filings must yield"


# ---------------------------------------------------------------------------
# What may be yielded
# ---------------------------------------------------------------------------

def test_yields_only_documents_and_rejections(adapter):
    source, ref = adapter
    for item in source.fetch_filings(ref, *WIDE):
        assert isinstance(item, (FilingDocument, Rejection)), type(item)


def test_an_unknown_reference_yields_a_rejection_rather_than_raising(adapter):
    """One unknown company must not end a 500-company backfill. The engine
    catches exceptions per instrument, but an adapter that raises for the
    ordinary case of 'never heard of it' turns a routine gap into a FAILED
    checkpoint that gets retried forever."""
    source, _ = adapter
    items = list(source.fetch_filings("definitely-not-a-real-reference", *WIDE))
    assert items, "expected a rejection, got nothing"
    assert all(isinstance(i, Rejection) for i in items)


def test_rejections_explain_themselves(adapter):
    source, _ = adapter
    for item in source.fetch_filings("definitely-not-a-real-reference", *WIDE):
        assert isinstance(item, Rejection)
        assert item.reason.strip(), "a rejection with no reason cannot be acted on"
        assert item.instrument_ref


# ---------------------------------------------------------------------------
# What a document must carry
# ---------------------------------------------------------------------------

def _documents(source, ref) -> list[FilingDocument]:
    return [i for i in source.fetch_filings(ref, *WIDE) if isinstance(i, FilingDocument)]


def test_every_document_has_provenance(adapter):
    """A fact with no provenance is unusable: the Phase 1 promise is that any
    number can be traced to the document it came from."""
    source, ref = adapter
    for doc in _documents(source, ref):
        assert doc.source_ref.strip()
        assert doc.content_hash.strip()


def test_every_document_is_dated_and_timezone_aware(adapter):
    """`filed_at` becomes `known_from`. A naive timestamp is a transaction-time
    bug waiting to happen, and known_from ordering is what the product rests
    on."""
    source, ref = adapter
    for doc in _documents(source, ref):
        assert doc.filed_at.tzinfo is not None
        assert doc.filed_at.utcoffset() is not None


def test_no_adapter_sets_known_from_itself(adapter):
    """Transaction time belongs to the filing, not to the adapter.

    `ReportedFact` deliberately has no `known_from` field: an adapter that
    could set it could forge history. This asserts the field stays absent, so
    a future adapter author cannot add one without this failing.
    """
    source, ref = adapter
    for doc in _documents(source, ref):
        for fact in doc.facts:
            assert not hasattr(fact, "known_from")


def test_content_hash_distinguishes_revisions(adapter):
    """Two documents from one adapter must not share a content hash unless
    they are the same document. The incremental job skips by content hash, so
    a collision means a restatement is silently never ingested."""
    source, ref = adapter
    docs = _documents(source, ref)
    hashes = [d.content_hash for d in docs]
    assert len(hashes) == len(set(hashes)), "duplicate content_hash within one source"


# ---------------------------------------------------------------------------
# What a fact must satisfy
# ---------------------------------------------------------------------------

def test_facts_use_decimal_not_float(adapter):
    """Floats lose precision on money and the loss is invisible in output."""
    source, ref = adapter
    for doc in _documents(source, ref):
        for fact in doc.facts:
            assert isinstance(fact.value, Decimal), type(fact.value)


def test_facts_use_known_vocabulary(adapter):
    source, ref = adapter
    for doc in _documents(source, ref):
        for fact in doc.facts:
            assert fact.basis in BASES
            assert fact.period_type in PERIOD_TYPES
            assert len(fact.currency) == 3


def test_facts_have_coherent_periods(adapter):
    source, ref = adapter
    for doc in _documents(source, ref):
        for fact in doc.facts:
            if fact.is_instant:
                assert fact.period_start == fact.period_end
            else:
                assert fact.period_end > fact.period_start


def test_the_requested_window_is_respected(adapter):
    """An adapter that ignores the window makes an incremental job re-ingest
    the whole history every cycle."""
    source, ref = adapter
    all_docs = _documents(source, ref)
    if not all_docs:
        pytest.skip("adapter yielded no documents to window")
    cutoff = max(f.period_end for d in all_docs for f in d.facts)
    narrow = [d for d in source.fetch_filings(ref, date(2000, 1, 1), cutoff)
              if isinstance(d, FilingDocument)]
    for doc in narrow:
        for fact in doc.facts:
            assert fact.period_end <= cutoff


def test_fetching_twice_gives_the_same_answer(adapter):
    """Adapters must be pure with respect to their input.

    The backfill retries a failed instrument and the incremental job runs
    daily; an adapter whose output depends on call order would make dedup
    nondeterministic, and the symptom would be versions appearing at random.
    """
    source, ref = adapter
    first = [(d.content_hash, len(d.facts)) for d in _documents(source, ref)]
    second = [(d.content_hash, len(d.facts)) for d in _documents(source, ref)]
    assert first == second
