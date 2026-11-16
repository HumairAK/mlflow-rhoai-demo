# Reset MLflow demo metadata

[reset-mlflow-db.py](../reset-mlflow-db.py) resets the dedicated PostgreSQL
`mlflow` database's `public` schema. It is tailored to this deployment:

- MLflow CR: `mlflow` (cluster-scoped).
- MLflow and its operator: `redhat-ods-applications`.
- PostgreSQL: Deployment `postgresql` in `mlflow-demo-db`, database/user `mlflow`.
- Tracking and model registry share that database; separate registry stores and
  read replicas are rejected by the script.

It deletes **all MLflow metadata in that schema across all workspaces**, including
experiments, runs, traces, prompt versions/aliases, models, and evaluation datasets.
This includes the experiment-RBAC test data, not just `mlflow-demo` data.
Kubernetes namespaces, RBAC, PostgreSQL, PVCs, Secrets, and MinIO are retained.
The script does not delete deployments, databases, or persistent volumes, but it
scales MLflow and its operator down temporarily. The migration flow may replace
its own migration Job as part of the normal operator behavior.

## Preview

From the demo checkout, using an authenticated `oc` context with administrative
permissions to the listed resources:

```bash
python3 setup/reset-mlflow-db.py
```

This is a read-only preflight. It prints the current cluster/context, database and
schema information, workload replica counts, and maintenance schedules. Verify
the cluster and targets before proceeding. No extra Python packages are required.
Credentials are read in memory, or used inside the existing PostgreSQL container,
and are not printed or passed as command-line passwords.

## Execute the reset

Stop sending chat requests and evaluations before starting. Existing agent pods
are retained; use a fresh chat after the reset rather than continuing old threads.
Other clients must not write to the database during the operation.

```bash
python3 setup/reset-mlflow-db.py --execute --confirm-database mlflow
```

The script:

1. Saves recovery state under a private timestamped directory in
   `~/.local/state/mlflow-demo/database-resets/`.
2. Scales the MLflow operator to zero so it does not immediately restore writers.
3. Suspends owned maintenance CronJobs and waits for existing maintenance and
   migration Jobs to finish. It does not terminate jobs or database sessions.
4. Scales MLflow-owned serving Deployments to zero and waits for their pods and
   remaining PostgreSQL sessions to exit. Unexpected writers prevent the reset.
5. Creates `mlflow-before.dump` with `pg_dump --format=custom` and checks that
   `pg_restore --list` can read the archive. Backups contain application data:
   keep them private and out of Git. Directories use mode 0700 and files 0600.
6. Rechecks quiescence, then drops and recreates only `public` in a transaction.
   Extra user schemas, public-schema extensions, unexpected ownership, or an
   unexpected database/user cause the script to stop.
7. Sets `mlflow.opendatahub.io/force-migrate` on the MLflow CR and restores the
   operator. The operator runs a fresh migration Job and restores serving.
8. Waits for a new successful migration, the force annotation to clear, and
   MLflow availability. It then restores saved replica counts and CronJob
   suspension flags.

The default timeout is 900 seconds per wait; `--timeout SECONDS` changes it.
If the platform unexpectedly restores a paused operator or serving workload,
pre-reset guards refuse to wipe the schema. Do not run platform upgrades or
other workload edits concurrently with the reset.

## Recovery after an interruption

The script prints the saved `state.json` path. If it stops, some workloads may
remain paused intentionally. Resume service initialization with:

```bash
python3 setup/reset-mlflow-db.py --resume /ABSOLUTE/PATH/TO/state.json
```

Resume **never drops the schema or repeats the reset**. It completes/retries the
forced migration and restores workloads. It checks that the cluster/context and
MLflow CR identity still match. If the failure occurred before the schema was
reset, resume migrates the existing database without clearing its data.

Resume is not a data rollback. The PostgreSQL custom-format archive is the data
backup; restoring it requires a separate, deliberate `pg_restore` while database
writers are stopped. Do not restore a dump into the running application.
If a command fails, its output is suppressed to avoid credential exposure;
inspect pod/Job status and logs as appropriate without dumping Secrets.

## Prepare the next recording

After success:

1. Seed the baseline prompt **once** in the empty registry.
2. Register the saved passing template as a new candidate when desired.
   Prompt version numbers restart; the previously passing v6 may become v2.
3. Create a fresh chat, trace, dataset, and evaluations for the recording.

The [passing template](../prompts/mortgage-lending-agent-v6.txt) is saved outside
MLflow, and the [walkthrough](demo-walkthrough.md) describes the recording flow.
MinIO artifact objects are not removed and may be orphaned after metadata reset.
LangGraph conversation state and in-flight/buffered client operations are separate
from PostgreSQL; the script does not clear or restart the agent.

Validation performed when preparing this script: read-only preflight against the
live deployment (70 public-schema tables), Python syntax, and mocked checks for
dry-run behavior, explicit execution confirmation, resume without reset, and
schema-target guards. The destructive execution path has not been run.
