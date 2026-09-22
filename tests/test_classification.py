"""SIC-to-sector mapping. Pure function, no network -- same reasoning
test_edgar.py gives for a cached fixture over a live call: the interesting
behaviour is our own bucketing, not SEC's uptime."""

from __future__ import annotations

from investment_intelligence.sources.classification import sector_for_sic


def test_known_codes_map_to_the_right_sector():
    assert sector_for_sic("7371") == "Services"          # Infosys: computer programming
    assert sector_for_sic("6021") == "Financial Services"  # a national commercial bank
    assert sector_for_sic("2834") == "Manufacturing"        # pharmaceutical preparations


def test_range_boundaries_are_inclusive_on_both_ends():
    assert sector_for_sic("2000") == "Manufacturing"
    assert sector_for_sic("3999") == "Manufacturing"
    assert sector_for_sic("1999") != "Manufacturing"  # one below the range
    assert sector_for_sic("4000") != "Manufacturing"  # one above the range


def test_unmapped_code_is_unclassified_not_a_crash():
    assert sector_for_sic("0") == "Unclassified"


def test_non_numeric_code_is_unclassified_not_a_crash():
    assert sector_for_sic("not-a-code") == "Unclassified"
    assert sector_for_sic(None) == "Unclassified"
