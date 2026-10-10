"""Command line: python -m data_platform <command>   (run from backend/)."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time

from . import db, repository
from .observability import configure_logging


def cmd_migrate(args):
    from .migrate import migrate_all

    print(json.dumps(migrate_all(), indent=2))


def cmd_sync(args):
    from .sources.registry import adapters, sync_to_control_plane

    sync_to_control_plane()
    for adapter in adapters().values():
        print(f"{adapter.source_id}: {', '.join(p.id for p in adapter.pipelines())}")


def _parse_params(values: list[str]) -> dict:
    params = {}
    for item in values or []:
        key, _, raw = item.partition("=")
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw
    return params


def cmd_run(args):
    from .engine.service import start_run
    from .engine.worker import Worker

    run = start_run(args.pipeline, _parse_params(args.param), trigger_type="cli", triggered_by="cli")
    print(f"queued run #{run['id']} ({run['concurrency_key']})")
    if not args.wait:
        return
    worker = Worker()
    worker.install_signal_handlers()
    configure_logging()
    import logging

    logging.getLogger().addHandler(worker._log_handler)
    while True:
        current = repository.get_run(run["id"])
        if current["status"] in ("COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_COMPLETED"):
            break
        if not worker.run_once():
            time.sleep(1)
    final = repository.get_run(run["id"])
    print(f"run #{final['id']}: {final['status']}" + (f" — {final['error_summary']}" if final["error_summary"] else ""))
    for stage in repository.get_stages(run["id"]):
        detail = stage["skip_reason"] or stage["error_message"] or ""
        print(f"  {stage['stage']:<18} {stage['status']:<10} out={stage['records_out'] or '':<10} {detail}")
    sys.exit(0 if final["status"] in ("COMPLETED", "PARTIALLY_COMPLETED") else 1)


def cmd_worker(args):
    from .engine.worker import Worker

    worker = Worker()
    worker.install_signal_handlers()
    worker.serve()


def cmd_runs(args):
    total, rows = repository.list_runs(limit=args.limit)
    for run in rows:
        print(f"#{run['id']:<5} {run['status']:<20} {run['pipeline_definition_id']:<14} {run['country'] or '':<4} "
              f"{run['created_at']:%Y-%m-%d %H:%M}  {run['error_summary'] or ''}")


def cmd_cancel(args):
    run = repository.request_cancel(args.run_id, "cli")
    print(f"run #{run['id']}: {run['status']} (cancel requested {run['cancel_requested_at']})")


def cmd_retry(args):
    from .engine.service import retry_run

    run = retry_run(args.run_id, "cli")
    print(f"queued retry run #{run['id']}")


def cmd_schedule(args):
    from .engine.scheduler import list_schedules, upsert_schedule

    if args.action == "list":
        for s in list_schedules():
            print(f"#{s['id']} {s['name']}: {s['pipeline_definition_id']} {s['cadence']} {s['cron'] or ''} "
                  f"next={s['next_run_at']} enabled={s['enabled']}")
        return
    schedule = upsert_schedule(args.pipeline, args.name, _parse_params(args.param), args.cadence, args.cron)
    print(f"created schedule #{schedule['id']}, next run at {schedule['next_run_at']}")


def cmd_api_key(args):
    from .api.auth import ALL_PERMISSIONS, create_api_key

    permissions = args.permission or sorted(ALL_PERMISSIONS)
    key = "dpk_" + secrets.token_urlsafe(32)
    create_api_key(args.name, key, permissions)
    print(f"API key for {args.name} (shown once): {key}")


def main():
    parser = argparse.ArgumentParser(prog="data_platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply control-plane and serving migrations").set_defaults(func=cmd_migrate)
    sub.add_parser("sync", help="register adapters, pipelines and datasets").set_defaults(func=cmd_sync)
    sub.add_parser("worker", help="run a worker (executes queued runs, schedules)").set_defaults(func=cmd_worker)

    run = sub.add_parser("run", help="queue a pipeline run")
    run.add_argument("pipeline")
    run.add_argument("--param", "-p", action="append", help="key=value (JSON values allowed)")
    run.add_argument("--wait", action="store_true", help="execute in this process and wait for the result")
    run.set_defaults(func=cmd_run)

    runs = sub.add_parser("runs", help="list recent runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.set_defaults(func=cmd_runs)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("run_id", type=int)
    cancel.set_defaults(func=cmd_cancel)

    retry = sub.add_parser("retry")
    retry.add_argument("run_id", type=int)
    retry.set_defaults(func=cmd_retry)

    schedule = sub.add_parser("schedule")
    schedule.add_argument("action", choices=["add", "list"])
    schedule.add_argument("--pipeline")
    schedule.add_argument("--name")
    schedule.add_argument("--cadence", choices=["manual", "daily", "weekly", "monthly", "cron", "on_release"])
    schedule.add_argument("--cron")
    schedule.add_argument("--param", "-p", action="append")
    schedule.set_defaults(func=cmd_schedule)

    api_key = sub.add_parser("api-key", help="create an API key (for DATA_PLATFORM_AUTH_MODE=api_key)")
    api_key.add_argument("name")
    api_key.add_argument("--permission", action="append")
    api_key.set_defaults(func=cmd_api_key)

    args = parser.parse_args()
    configure_logging("WARNING" if args.command not in ("worker",) else "INFO")
    try:
        args.func(args)
    finally:
        db.close_pools()


if __name__ == "__main__":
    main()
