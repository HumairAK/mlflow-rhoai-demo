import json
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

base = subprocess.check_output(
    ["oc", "get", "mlflow", "mlflow", "-o", "jsonpath={.status.url}"], text=True
).strip().rstrip("/")
assert base.startswith("https://"), "MLflow status.url must be populated"
token = subprocess.check_output(["oc", "whoami", "-t"], text=True).strip()
headers = {"Authorization": f"Bearer {token}", "X-MLFLOW-WORKSPACE": "mlflow-demo"}

def request(method, path, body=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    req = Request(base + path, data=data, method=method, headers={
        **headers, "Content-Type": "application/octet-stream" if raw else "application/json"
    })
    try:
        with urlopen(req, timeout=60) as response:
            content = response.read()
            return content if raw else (json.loads(content) if content else {})
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}: {exc.read().decode()[:1000]}") from None

request("GET", "/api/3.0/mlflow/server-info")
workspaces = request("GET", "/ajax-api/3.0/mlflow/workspaces")
assert any(w["name"] == "mlflow-demo" for w in workspaces["workspaces"])
experiment = request("POST", "/api/2.0/mlflow/experiments/create", {
    "name": f"storage-smoke-{time.time_ns()}"
})["experiment_id"]
run_id = None
try:
    info = request("POST", "/api/2.0/mlflow/runs/create", {
        "experiment_id": experiment, "start_time": int(time.time() * 1000)
    })["run"]["info"]
    run_id = info["run_id"]
    artifact = urlsplit(info["artifact_uri"])
    assert artifact.scheme == "mlflow-artifacts", info["artifact_uri"]
    path = "/api/2.0/mlflow-artifacts/artifacts/" + quote(
        artifact.path.strip("/") + "/storage-smoke.txt", safe="/"
    )
    content = b"MLflow PostgreSQL and MinIO TLS verification\n"
    request("PUT", path, content, raw=True)
    assert request("GET", path, raw=True) == content
    listing = request("GET", f"/api/2.0/mlflow/artifacts/list?run_id={run_id}")
    assert any(f["path"] == "storage-smoke.txt" for f in listing["files"])
    request("POST", "/api/2.0/mlflow/runs/update", {
        "run_id": run_id, "status": "FINISHED", "end_time": int(time.time() * 1000)
    })
    print(f"PASS: workspace discovery, metadata writes, artifact upload/list/download at {base}")
    print(f"Smoke experiment={experiment}, run={run_id}; soft-deleting the test records.")
finally:
    if run_id:
        request("POST", "/api/2.0/mlflow/runs/delete", {"run_id": run_id})
    request("POST", "/api/2.0/mlflow/experiments/delete", {"experiment_id": experiment})