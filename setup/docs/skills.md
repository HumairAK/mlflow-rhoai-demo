# Mock mortgage skills

The demo includes three sample skills stored as `SKILL.md` files in
[setup/skills](../skills):

| Skill | Example purpose |
| --- | --- |
| `mortgage-pricing-verification` | Verify a requested rate against the pricing tool and explain exception requirements. |
| `mortgage-borrower-follow-up` | Draft a clear follow-up with verified pricing, lock status, and actionable next steps. |
| `mortgage-sensitive-data-protection` | Review a draft for full SSNs and avoid disclosing them. |

These are fictional demo instructions registered in MLflow's Skills registry.
They are separate from the agent's production prompt. Registration makes them
available to inspect and version; the running mortgage agent does not yet load
or execute them. Its existing prompt-injection and evaluation behavior is unchanged.

## Register without resetting

From the demo checkout, preview the files without connecting to the cluster:

```bash
python3 setup/seed_mlflow_skills.py
```

Register them using your current `oc login` session:

```bash
python3 setup/seed_mlflow_skills.py --execute
```

The helper discovers MLflow's public URL from the `mlflow` custom resource and
uses workspace `mlflow-demo`. It reads `oc whoami -t` into memory without printing
or saving the token. No environment sourcing or extra Python packages are required.
The current user needs skill read/list/create/update access in that workspace.
The agent keeps its existing skill read/list permissions.

The helper uploads only each reviewed `SKILL.md`, not the surrounding checkout.
MLflow stores the versioned content in its configured artifact storage. Repeated
runs reuse the latest version when its content digest matches; editing the content
creates another version. The script does not remove other skills or versions.

The deployed server supports the Skills REST API, while this demo's MLflow 3.14
client does not include the newer Skills SDK. The helper uses the REST upload API
so no SDK upgrade or agent image rebuild is needed. TLS verification remains enabled.

## Reset behavior and showing the registry

The [database reset script](reset-mlflow-data.md) calls this helper after the
agent restarts and the baseline prompt is seeded. If registration is interrupted,
`--resume` retries skill registration without repeating the database reset.

In MLflow, select workspace `mlflow-demo` and open the Skills registry. Show the
skill description, version, and stored `SKILL.md` content. These skills demonstrate
registry management; describing them as active agent capabilities would require
additional application integration.
