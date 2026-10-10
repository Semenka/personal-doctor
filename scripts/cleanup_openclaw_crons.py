"""Remove legacy health cron jobs by ID without touching managed agent jobs."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time


NAMES = {'personal-doctor-daily', 'personal-doctor-oura', 'personal-doctor-gdrive'}


def parse_output(text):
    for pos, char in enumerate(text):
        if char not in '[{':
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(text[pos:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get('jobs'), list):
            return value
    raise ValueError('OpenClaw did not return a jobs list')


def select_jobs(jobs, include_managed=False):
    return [job for job in jobs if job.get('name') in NAMES and job.get('id')
            and (include_managed or job.get('agentId') in (None, '', 'main'))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--include-managed', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    proc = subprocess.run(['openclaw', 'cron', 'list', '--all', '--json'],
                          capture_output=True, text=True, check=True, timeout=60)
    jobs = select_jobs(parse_output(proc.stdout)['jobs'], args.include_managed)
    if jobs and not args.dry_run:
        backup = pathlib.Path.home() / '.openclaw' / 'state' / f'legacy-health-crons-{time.time_ns()}.json'
        backup.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
            json.dump(jobs, handle, indent=2)
        print(f'Backup: {backup}', flush=True)
    for job in jobs:
        if not args.dry_run:
            subprocess.run(['openclaw', 'cron', 'rm', job['id']], check=True, timeout=60)
        print(f'{"Would remove" if args.dry_run else "Removed"}: {job["name"]} ({job["id"]})', flush=True)
    print(f'Legacy health jobs: {len(jobs)}')


if __name__ == '__main__':
    main()
