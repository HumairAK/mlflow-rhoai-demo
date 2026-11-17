# Reset MLflow demo metadata

[reset-mlflow-db.py](../reset-mlflow-db.py) resets the dedicated PostgreSQL
`mlflow` database's `public` schema. It is tailored to this deployment:

- MLflow CR: `mlflow` (cluster-scoped).
- MLflow and its operator: `redhat-ods-applications`.
- PostgreSQL: Deployment `postgresql` in `mlflow-demo-db`, database/user `mlflow`.
- Agent Sandbox and pod: `fsi-mortgage-agent` in `mlflow-demo` (must be Running).
- Tracking and model registry share that database; separate registry stores and
  read replicas are rejected by the script.

It deletes **all MLflow metadata in that schema across all workspaces**, including
experiments, runs, traces, prompt versions/aliases, models, and evaluation datasets.
This includes the experiment-RBAC test data, registered Skills, and MCP registry
records, not just `mlflow-demo` data.
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

Stop sending chat requests and evaluations before starting. The agent pod is
restarted after MLflow recovers; use a fresh chat rather than continuing old threads.
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
9. Deletes the `fsi-mortgage-agent` pod and waits for the Agent Sandbox operator
   to recreate a ready pod. This restarts both backend and UI, clearing the old
   experiment ID cached by the backend. Startup selects or creates
   `fsi-mortgage-demo` in the empty database. The Sandbox itself is retained.
10. Seeds the weak baseline `mortgage-lending-agent` prompt and points its
    `production` alias at v1. This runs inside the backend using its existing
    ServiceAccount and workspace configuration; no local Python dependencies,
    environment sourcing, or personal token are needed for seeding.
11. Registers the three [mock mortgage skills](skills.md) in workspace
    `mlflow-demo`. This uses the current `oc` user's token in memory, since the
    agent's existing skill permissions are read-only. Unchanged skill content is
    reused on retries. No agent image rebuild is required.
12. Restores the saved [MCP catalog registrations](mcp-servers.md) for MongoDB,
    OpenShift, and Confluent, including their versions and 37 tool definitions.
    This registers metadata using the current `oc` identity; it does not deploy
    MCP servers or call their tools.

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
forced migration, restores workloads, and finishes the agent restart, prompt
seeding, skill registration, and MCP registration. If only the restart or seeding
remains, it skips
migration; it records the old pod UID so it does
not delete an already-created replacement pod. It checks that the cluster/context,
MLflow CR, and agent Sandbox identity still match. If the failure occurred before
the schema was reset, resume migrates the existing database without clearing its data.
If seeding was interrupted after registration, resume reuses the baseline v1
and sets its alias, avoiding an additional version. An unexpected v1 template
stops recovery instead of changing the production alias.
If skill registration fails, resume retries that step without rerunning the
database migration, agent restart, or prompt seeding.
If MCP registration fails, resume retries only that final step, without repeating
skill registration or any earlier completed stage.

Resume is not a data rollback. The PostgreSQL custom-format archive is the data
backup; restoring it requires a separate, deliberate `pg_restore` while database
writers are stopped. Do not restore a dump into the running application.
If a command fails, its output is suppressed to avoid credential exposure;
inspect pod/Job status and logs as appropriate without dumping Secrets.

## Prepare the next recording

After success:

1. Verify `mortgage-lending-agent@production` points at the weak baseline v1.
   The script seeds it automatically; do not run the seed command again.
   The three mock skills should also appear in MLflow's Skills registry in
   workspace `mlflow-demo`.
   The three saved catalog records should appear in the MCP server registry.
2. Register the saved passing template as a new candidate when desired.
   Prompt version numbers restart; the previously passing v6 may become v2.
3. Create a fresh chat, trace, dataset, and evaluations for the recording.

The [passing template](../prompts/mortgage-lending-agent-v6.txt) is saved outside
MLflow, and the [walkthrough](demo-walkthrough.md) describes the recording flow.
MinIO artifact objects are not removed and may be orphaned after metadata reset.
The agent restart clears process memory, including the cached experiment ID.
LangGraph may retain local conversation checkpoints; the script does not explicitly
delete those files. Refresh the browser and start a fresh chat after the script completes.

Validation performed when preparing this script: read-only preflight against the
live deployment (70 public-schema tables), Python syntax, and mocked checks for
dry-run behavior, explicit execution confirmation, resume without reset, and
schema-target guards. The restart addition also passed seven mocked checks covering
restart ordering, replacement readiness, ownership, stopped Sandboxes, dry-run
behavior, and resume without repeated deletion or migration. Six additional mocked
checks cover prompt seeding order, interrupted seeding, reuse of v1, template
validation, and error handling. The updated destructive execution path has not
been run.

Skill registration was also verified against the running deployment: all three
v1 artifacts were downloaded and compared with the local manifests; a second
registration reused v1; and the agent's ServiceAccount could list the skills.
Offline skill tests cover upload retries, changed content, API availability,
reset ordering, dry runs, and resuming only skill registration after a failure.
The saved MCP registrations were replayed against the live server and reused
their existing versions. Offline tests cover restoring all 37 tool definitions,
retrying partial registration, semantic-version conflicts, and resuming only MCP
registration. Validation did not reset the database or deploy MCP workloads.
