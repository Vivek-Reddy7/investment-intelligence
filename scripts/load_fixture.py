"""Load the cached EDGAR fixture into the database, without touching the network.

Used by CI so the API contract test has real data to assert against. The
alternative is calling the SEC on every push, which is rude and makes the
build depend on someone else's uptime.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investment_intelligence.analytics.screen import refresh          # noqa: E402
from investment_intelligence.db import connect                        # noqa: E402
from investment_intelligence.ingest import backfill                   # noqa: E402
from investment_intelligence.sources.edgar import EdgarSource         # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "edgar_infy.json"
INFY_CIK = 1067491


def main() -> None:
    payload = json.loads(FIXTURE.read_text())
    # Only Infosys is in the fixture; every other tracked company will be
    # rejected as an unknown reference, which is correct and is what the gap
    # report is for.
    source = EdgarSource(cik_by_ref={str(INFY_CIK): INFY_CIK},
                         fetch=lambda cik: payload)
    with connect() as conn:
        report = backfill.run(conn, source, job="ci-fixture",
                              since=date(2010, 1, 1), until=date(2040, 1, 1))
        rows = refresh(conn)
        conn.commit()
    print(f"loaded {report.writes.facts_written} facts, {rows} metric rows "
          f"(outcome={report.outcome})")


if __name__ == "__main__":
    main()
