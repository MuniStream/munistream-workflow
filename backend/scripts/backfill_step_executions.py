#!/usr/bin/env python3
"""
Backfill StepExecution records from the historical STEP_ADVANCED event stream.

The DAG executor only started persisting per-step timing (StepExecution rows)
recently. For instances that ran before that change there is no per-step timing
except the STEP_ADVANCED events in `workflow_events`, which stamp the moment each
step handed off to the next. This script reconstructs per-step start/end times by
diffing consecutive STEP_ADVANCED timestamps per instance and writes StepExecution
rows so workflow analytics have real historical data.

Idempotency: it uses a coarse guard — if any StepExecution already exists for an
(instance_id, step_id) pair it skips that step. Historical instances have zero
rows and get backfilled once; instances written by the live executor already have
rows and are skipped entirely. (This collapses historical retries of the same step
into a single row, which is acceptable for historical data.)

Usage:
    python scripts/backfill_step_executions.py [--workflow-id WF] [--dry-run] [--batch-size N]
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

# Add the project root to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.database import connect_to_mongo, close_mongo_connection
from app.models.workflow import WorkflowEvent, WorkflowInstance, StepExecution, EventType
from app.services.workflow_service import StepExecutionService


def _reconstruct_steps(instance, events):
    """Reconstruct (step_id, started_at, completed_at, status) tuples for one instance.

    `events` are that instance's STEP_ADVANCED events sorted by timestamp asc.
    Each event means: previous_step finished at event.timestamp and current_step
    started at event.timestamp. Assumes a linear path (loops collapse via the
    caller's coarse guard).
    """
    if not events:
        return []

    # Ordered step sequence: first event's previous_step, then every current_step.
    seq = []
    first_prev = (events[0].event_data or {}).get("previous_step")
    if first_prev:
        seq.append(first_prev)
    for e in events:
        cur = (e.event_data or {}).get("current_step")
        seq.append(cur)

    failed = set(instance.failed_steps or [])
    skipped = set(instance.skipped_steps or [])
    instance_completed = instance.status == "completed"

    reconstructed = []
    for i, step_id in enumerate(seq):
        if not step_id:
            continue
        # Transition INTO this step.
        started_at = instance.started_at if i == 0 else events[i - 1].timestamp
        # Transition OUT of this step.
        if i < len(events):
            completed_at = events[i].timestamp
        else:
            # Final step: only reliable when the instance actually finished.
            if not instance_completed or not instance.completed_at:
                continue
            completed_at = instance.completed_at

        if not started_at or not completed_at:
            continue

        if step_id in failed:
            status = "failed"
        elif step_id in skipped:
            status = "skipped"
        else:
            status = "completed"

        reconstructed.append((step_id, started_at, completed_at, status))

    return reconstructed


async def backfill(workflow_id=None, dry_run=False, batch_size=500):
    query = {"event_type": EventType.STEP_ADVANCED}
    if workflow_id:
        query["workflow_id"] = workflow_id

    events = await WorkflowEvent.find(query).sort("+instance_id", "+timestamp").to_list()

    events_by_instance = defaultdict(list)
    for e in events:
        if e.instance_id:
            events_by_instance[e.instance_id].append(e)

    print(f"Found {len(events)} STEP_ADVANCED events across {len(events_by_instance)} instances"
          + (f" for workflow {workflow_id}" if workflow_id else ""))

    instances_scanned = 0
    instances_skipped_no_instance = 0
    rows_written = 0
    rows_skipped_existing = 0

    for instance_id, inst_events in events_by_instance.items():
        instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
        if not instance:
            instances_skipped_no_instance += 1
            continue
        instances_scanned += 1

        # Coarse idempotency guard: step_ids already recorded for this instance.
        existing = await StepExecution.find(StepExecution.instance_id == instance_id).to_list()
        seen_steps = {se.step_id for se in existing}

        for step_id, started_at, completed_at, status in _reconstruct_steps(instance, inst_events):
            if step_id in seen_steps:
                rows_skipped_existing += 1
                continue
            seen_steps.add(step_id)
            if dry_run:
                rows_written += 1
                continue
            await StepExecutionService.upsert_step_execution(
                instance_id=instance_id,
                step_id=step_id,
                workflow_id=instance.workflow_id,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
            )
            rows_written += 1

    verb = "would write" if dry_run else "wrote"
    print("---")
    print(f"Instances scanned:          {instances_scanned}")
    print(f"Instances skipped (missing): {instances_skipped_no_instance}")
    print(f"StepExecution rows {verb}:   {rows_written}")
    print(f"Rows skipped (already present): {rows_skipped_existing}")


async def main():
    parser = argparse.ArgumentParser(description="Backfill StepExecution rows from STEP_ADVANCED events")
    parser.add_argument("--workflow-id", default=None, help="Restrict to a single workflow_id")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing")
    parser.add_argument("--batch-size", type=int, default=500, help="Reserved for future batching")
    args = parser.parse_args()

    await connect_to_mongo()
    print("Connected to MongoDB")
    try:
        await backfill(workflow_id=args.workflow_id, dry_run=args.dry_run, batch_size=args.batch_size)
    finally:
        await close_mongo_connection()
        print("Disconnected")


if __name__ == "__main__":
    asyncio.run(main())
