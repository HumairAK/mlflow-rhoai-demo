# Restoring MCP server registrations

The reset script restores these existing catalog records in workspace `mlflow-demo`
after restarting the agent, seeding its baseline prompt, and registering mock skills:

| Server | Version | Saved tools |
| --- | --- | --- |
| `com.mongodb/mongodb-mcp-server` | `1.0.0` | 15 |
| `com.redhat/openshift-mcp-server` | `0.4.0` | 15 |
| `io.confluent/mcp-confluent` | `1.0.0` | 7 |

The [saved definitions](../mcp-servers.json) were exported from the current
MLflow deployment. They include parent metadata, icons, catalog-source tags,
versioned `server_json`, status, deployment metadata, and tool schemas. No aliases
or access endpoints were registered when the export was taken.

These are registry records. Restoring them does not deploy an MCP server, call
its tools, discover tools from an external service, create Kubernetes Secrets,
or change the mortgage agent's tools. The agent continues to use its existing
local mortgage-pricing tool.

## Run separately

Preview the saved records locally:

```bash
python3 setup/seed_mlflow_mcp_servers.py
```

Restore them without resetting the database:

```bash
python3 setup/seed_mlflow_mcp_servers.py --execute
```

The helper uses the current `oc login` session, discovers the MLflow URL from
the cluster-scoped `mlflow` resource, and registers in `mlflow-demo`. It requires
MCP registry read/list/create/update permissions in that workspace. Tokens stay
in memory. The agent's read-only registry permissions are unchanged.

Existing versions are reused; unchanged reruns create no duplicate records.
If a saved semantic version already exists with different `server_json` or
source, the helper stops instead of replacing that definition. Mutable metadata,
tool schemas, and status are restored to the saved values. It does not remove
other versions, registrations, tags, aliases, or endpoints.

Reset recovery records a separate `seeding-mcp-servers` phase. If this step fails,
use the printed `--resume` command; it retries MCP registration without repeating
the database reset, migration, agent restart, prompt seeding, or skill registration.

## Credentials and portability

The saved definitions contain no credential values. Environment settings contain
placeholders such as `<CONFLUENT_CLOUD_API_KEY>` or references to Kubernetes
Secrets. Those Secrets are not read or exported. They are still needed if you
later choose to deploy and connect to these servers.

Catalog icon URLs refer to this demo's RHOAI hostname and catalog entries. Update
them when moving the demo to another cluster. Container references, including
partner and Red Hat registry images, retain their existing pull requirements.

The three records and their 37 tool definitions were compared against the live
registry. Empty-registry creation and interrupted recovery are covered by offline
tests; the database was not reset for validation.
