"""Command line for the scraping engine: python -m data_platform.scrape <command>   (run from backend/).

Agents (long-running; start several fetchers/parsers to scale):
    frontier | fetcher | parser | enricher | writer | all (every agent in one process, for development)
Operations:
    seed, ingest, cancel, status, export, proxy-check, dlq, sweep, schemas

Crawl results reach Gold through the `websites.profiles` pipeline (queued automatically when a campaign
completes, or by `ingest`): Bronze -> silver.domain_facts -> gold.domain_profiles -> serving.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from functools import partial

from .. import db
from ..observability import configure_logging


def _agents(args) -> dict:
    from . import enricher, fetcher, frontier, parser, writer

    return {
        "frontier": frontier.run,
        "fetcher": partial(fetcher.run, concurrency=args.concurrency or 64),
        "parser": partial(parser.run, concurrency=args.concurrency or 4),
        "enricher": partial(enricher.run, concurrency=args.concurrency or 1),
        "writer": writer.run,
    }


def cmd_agent(args):
    from .streams import run_until_signalled

    agents = _agents(args)
    run_until_signalled(*(agents.values() if args.command == "all" else [agents[args.command]]))


def cmd_seed(args):
    from .frontier import seed

    campaign = seed(args.country, limit=args.limit, domains=args.domain, max_pages=args.max_pages,
                    enrich=not args.no_enrich, only_live=not args.include_unverified, ingest=not args.no_ingest)
    print(f"campaign #{campaign['id']}: {campaign['domain_count']} domains queued for crawling")


def cmd_ingest(args):
    from .frontier import ingest

    run = ingest(args.country.upper())
    print(f"queued run #{run['id']} (websites.profiles {args.country.upper()}); a worker executes it"
          if run else "could not queue the pipeline; see the log")


def cmd_cancel(args):
    from .frontier import cancel

    cancel(args.campaign_id)
    print(f"campaign #{args.campaign_id} cancelled (jobs already in flight still finish)")


def cmd_status(args):
    from . import blobstore
    from .messages import DLQ, RETRY
    from .proxies import COOLDOWN_KEY, FAIL_KEY, OK_KEY
    from .streams import connect_sync

    r = connect_sync()
    print("queues (outstanding = waiting + in progress)")
    for stream, group in (("scrape:requests", "fetcher"), ("scrape:fetched", "parser"), ("scrape:parsed", "writer"),
                          ("enrich:requests", "enricher"), ("enrich:results", "writer")):
        length = r.xlen(stream)
        try:
            pending = r.xpending(stream, group)["pending"]
            consumers = len(r.xinfo_consumers(stream, group))
        except Exception:
            pending = consumers = 0
        print(f"  {stream:<18} outstanding={length:<7} in_progress={pending:<6} consumers={consumers}")
    print(f"  {'retry (delayed)':<18} {r.zcard(RETRY)}")
    print(f"  {'dead letters':<18} {r.xlen(DLQ)}")
    cooling = r.zcount(COOLDOWN_KEY, time.time(), "+inf")
    ok, fail = sum(map(int, r.hvals(OK_KEY) or [0])), sum(map(int, r.hvals(FAIL_KEY) or [0]))
    print(f"proxies: {cooling} cooling down; requests ok={ok} failed={fail}")
    files, size = blobstore.usage()
    print(f"blobs: {files} files, {size / 1e6:.1f} MB")

    with db.serving() as conn:
        campaigns = conn.execute("""
            SELECT * FROM web.crawl_campaigns ORDER BY id DESC LIMIT %s
        """, [1 if args.campaign is None else 1000]).fetchall()
        for c in campaigns:
            if args.campaign is not None and c["id"] != args.campaign:
                continue
            urls = conn.execute("SELECT state, count(*) AS n FROM web.crawl_urls WHERE campaign_id = %s GROUP BY 1",
                                [c["id"]]).fetchall()
            domains = conn.execute("SELECT state, count(*) AS n FROM web.crawl_domains WHERE campaign_id = %s "
                                   "GROUP BY 1", [c["id"]]).fetchall()
            errors = conn.execute("""
                SELECT error_class, count(*) AS n FROM web.crawl_urls WHERE campaign_id = %s AND state = 'failed'
                GROUP BY 1 ORDER BY 2 DESC
            """, [c["id"]]).fetchall()
            print(f"\ncampaign #{c['id']} {c['status']} ({c['domain_count']} domains, started {c['created_at']:%Y-%m-%d %H:%M})")
            print("  domains: " + ", ".join(f"{r['state']}={r['n']}" for r in domains))
            print("  pages:   " + ", ".join(f"{r['state']}={r['n']}" for r in urls))
            if errors:
                print("  errors:  " + ", ".join(f"{r['error_class']}={r['n']}" for r in errors))


EXPORT_COLUMNS = ("domain", "company_name", "summary", "industry", "category_group", "city", "founded_year",
                  "employees", "market_cap", "revenue", "financial_info", "facebook", "instagram", "x_twitter",
                  "linkedin", "youtube", "tiktok", "pinterest", "whatsapp", "threads", "emails", "phones", "sources",
                  "pages_crawled", "field_sources")


def cmd_export(args):
    with db.serving() as conn:
        campaign = args.campaign or conn.execute("SELECT max(id) AS id FROM web.crawl_campaigns").fetchone()["id"]
        rows = conn.execute(f"""
            SELECT {", ".join("p." + c for c in EXPORT_COLUMNS)}, d.state AS crawl_state,
                   (SELECT string_agg(u.url || ' [' || u.state || coalesce(':' || u.error_class, '') || ']', ' | '
                                      ORDER BY u.depth, u.priority)
                    FROM web.crawl_urls u WHERE u.campaign_id = d.campaign_id AND u.domain = d.domain
                      AND u.state <> 'skipped') AS pages
            FROM web.crawl_domains d
            LEFT JOIN gold.domain_profiles p ON p.domain = d.domain AND p.country_code = d.country_code
            WHERE d.campaign_id = %s ORDER BY d.domain
        """, [campaign]).fetchall()
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    writer = csv.writer(out)
    writer.writerow([*EXPORT_COLUMNS, "crawl_state", "pages"])
    for row in rows:
        writer.writerow([" ; ".join(v) if isinstance(v, list) else json.dumps(v) if isinstance(v, dict) else v
                         for v in row.values()])
    if args.out:
        out.close()
        print(f"wrote {len(rows)} domains of campaign #{campaign} to {args.out}")


def cmd_proxy_check(args):
    from . import proxies
    from .settings import get_scrape_settings
    from .streams import connect

    pool = proxies.load(get_scrape_settings().proxies_file)
    if not pool:
        print("no proxies configured (set DP_SECRET_PROXIES_FILE or create ~/.config/xentolab/proxies.json)")
        return

    async def main():
        r = connect()
        try:
            return await proxies.check(pool, r)
        finally:
            await r.aclose()

    results = asyncio.run(main())
    for res in sorted(results, key=lambda x: (not x["ok"], x["ms"])):
        print(f"  {res['id']}  {res['host']:<22} {'OK ' if res['ok'] else 'BAD'} {res['ms']:>6} ms  "
              f"{res['exit_ip'] or res['error'] or ''}")
    print(f"{sum(r['ok'] for r in results)}/{len(results)} proxies healthy")


def cmd_dlq(args):
    from .messages import DLQ
    from .streams import connect_sync

    r = connect_sync()
    entries = r.xrange(DLQ, count=args.limit)
    for id, entry in entries:
        print(f"{id}  {entry['stream']:<16} deliveries={entry.get('deliveries')}  {entry['error'][:150]}")
        if args.requeue:
            r.xadd(entry["stream"], {"m": entry["m"]})
            r.xdel(DLQ, id)
    print(f"{len(entries)} dead letter(s){' re-queued' if args.requeue and entries else ''}")


def cmd_sweep(args):
    from . import blobstore

    files, size = blobstore.sweep(args.hours)
    print(f"deleted {files} blob(s), {size / 1e6:.1f} MB")


def cmd_schemas(args):
    from .messages import MESSAGES, json_schema

    print(json.dumps({cls.__name__: json_schema(cls) for cls in MESSAGES}, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="data_platform.scrape")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("frontier", "fetcher", "parser", "enricher", "writer", "all"):
        agent = sub.add_parser(name, help=f"run the {name} agent" if name != "all" else "run every agent here")
        agent.add_argument("--concurrency", type=int, help="jobs in flight (fetcher 64, parser 4, enricher 1)")
        agent.set_defaults(func=cmd_agent)

    seed = sub.add_parser("seed", help="start a crawl campaign")
    seed.add_argument("--country", default="PK")
    seed.add_argument("--limit", type=int, default=10, help="top N business-candidate domains")
    seed.add_argument("--domain", action="append", help="crawl these domains instead (repeatable)")
    seed.add_argument("--max-pages", type=int, help="pages per domain (default SCRAPE_MAX_PAGES_PER_DOMAIN)")
    seed.add_argument("--no-enrich", action="store_true", help="skip the LLM step")
    seed.add_argument("--no-ingest", action="store_true",
                      help="do not queue the websites.profiles pipeline when the campaign completes")
    seed.add_argument("--include-unverified", action="store_true",
                      help="also domains whose homepage was not seen live by Common Crawl")
    seed.set_defaults(func=cmd_seed)

    ingest = sub.add_parser("ingest", help="queue the websites.profiles pipeline (collected facts -> Gold)")
    ingest.add_argument("--country", default="PK")
    ingest.set_defaults(func=cmd_ingest)

    cancel = sub.add_parser("cancel", help="stop a campaign")
    cancel.add_argument("campaign_id", type=int)
    cancel.set_defaults(func=cmd_cancel)

    status = sub.add_parser("status", help="queues, proxies, blobs and campaign progress")
    status.add_argument("--campaign", type=int)
    status.set_defaults(func=cmd_status)

    export = sub.add_parser("export", help="Gold domain profiles of a campaign's domains as CSV")
    export.add_argument("--campaign", type=int, help="default: the latest")
    export.add_argument("--out", help="file path (default: stdout)")
    export.set_defaults(func=cmd_export)

    sub.add_parser("proxy-check", help="probe every proxy").set_defaults(func=cmd_proxy_check)

    dlq = sub.add_parser("dlq", help="list dead-lettered messages")
    dlq.add_argument("--limit", type=int, default=50)
    dlq.add_argument("--requeue", action="store_true", help="put them back on their stream")
    dlq.set_defaults(func=cmd_dlq)

    sweep = sub.add_parser("sweep", help="delete stale blobs")
    sweep.add_argument("--hours", type=float, help="older than (default SCRAPE_BLOB_TTL_HOURS)")
    sweep.set_defaults(func=cmd_sweep)

    sub.add_parser("schemas", help="print the JSON Schema of every message").set_defaults(func=cmd_schemas)

    args = parser.parse_args()
    configure_logging("INFO" if args.func is cmd_agent else "WARNING")
    try:
        args.func(args)
    finally:
        db.close_pools()


if __name__ == "__main__":
    main()
