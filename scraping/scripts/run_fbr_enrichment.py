"""Run the FBR company enrichment job (same as `python -m app.cli run-job --job fbr_company_enrichment`).

    python scripts/run_fbr_enrichment.py [--limit N] [--resume]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main

JOB = "fbr_company_enrichment"


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="process at most N new companies")
    parser.add_argument("--resume", action="store_true", help="continue the latest run")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    main.configure_logging(JOB, verbose=args.verbose)
    stats = main.run_job(JOB, resume=args.resume, limit=args.limit)
    print(f"{stats.status}: items={dict(stats.items)} records={stats.records} in {stats.duration_s}s")


if __name__ == "__main__":
    cli()
