from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import EXAMPLE_TRACES
from traceforge.api.app import create_app
from traceforge.config import Settings

KEY = {"X-API-Key": "test-key"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):  # type: ignore[no-untyped-def]
    settings = Settings(env="test", data_dir=tmp_path_factory.mktemp("api"), api_key="test-key")
    with TestClient(create_app(settings)) as c:
        yield c


def wait(client: TestClient, url: str) -> dict:  # type: ignore[type-arg]
    for _ in range(300):
        body = client.get(url, headers=KEY).json()
        if body["status"] in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"):
            return body
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_health_and_auth(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready").json()
    assert ready["status"] == "ready" and ready["checks"]["migrations_current"]
    r = client.get("/api/v1/tasks")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/v1/tasks", headers={"X-API-Key": "wrong"}).status_code == 401
    assert "X-Correlation-ID" in r.headers
    assert (
        client.get("/health/live", headers={"X-Correlation-ID": "abc"}).headers["X-Correlation-ID"]
        == "abc"
    )


def test_tasks_pagination_and_public_spec_only(client: TestClient) -> None:
    page = client.get("/api/v1/tasks?limit=5&offset=5", headers=KEY).json()
    assert page["total"] == 39 and len(page["items"]) == 5 and page["offset"] == 5
    spec = client.get("/api/v1/tasks/ssrf-t0-s000", headers=KEY).json()
    assert "hidden_test_module" not in spec and "fixture_hash" not in str(spec)
    assert client.get("/api/v1/tasks?limit=1000", headers=KEY).status_code == 422
    created = client.post(
        "/api/v1/tasks", json={"family": "ssrf", "template_id": "t0", "seed": 77}, headers=KEY
    )
    assert created.status_code == 201 and created.json()["task_id"] == "ssrf-t0-s077"
    assert (
        client.post(
            "/api/v1/tasks", json={"family": "nope", "template_id": "t0", "seed": 1}, headers=KEY
        ).status_code
        == 404
    )


def test_rollout_job_lifecycle(client: TestClient) -> None:
    r = client.post(
        "/api/v1/rollouts",
        json={"task_id": "authz-t1-s000"},
        headers={**KEY, "Idempotency-Key": "r1"},
    )
    assert r.status_code == 202
    job = wait(client, r.json()["status_url"])
    assert job["status"] == "SUCCEEDED" and job["output"]["verdict"] == "PASS"
    rollout = client.get(r.json()["resource_url"], headers=KEY).json()
    assert rollout["reward"]["final_reward"] > 0.9
    replay = client.post(
        "/api/v1/rollouts",
        json={"task_id": "authz-t1-s000"},
        headers={**KEY, "Idempotency-Key": "r1"},
    )
    assert replay.json()["job_id"] == r.json()["job_id"]
    v = client.post(
        "/api/v1/verifications",
        json={
            "rollout_id": r.json()["resource_id"],
            "profile": "v1_self_report",
            "reward_policy": "self-report-r1",
        },
        headers=KEY,
    ).json()
    assert (
        v["verification"]["authoritative"] is False and v["reward"]["policy_id"] == "self-report-r1"
    )
    assert (
        client.get(
            f"/api/v1/verifications/{job['output']['verification_id']}", headers=KEY
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/rewards/{job['output']['reward_id']}", headers=KEY).status_code == 200
    )


def test_errors_are_enveloped(client: TestClient) -> None:
    r = client.post("/api/v1/rollouts", json={"task_id": "does-not-exist"}, headers=KEY)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/api/v1/rollouts", json={"task_id": "sqli-t0-s000", "extra": 1}, headers=KEY)
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"]
    r = client.post(
        "/api/v1/rollouts",
        json={
            "task_id": "sqli-t0-s000",
            "provider": {"kind": "openai_compatible", "base_url": "http://x"},
        },
        headers=KEY,
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "capability_unavailable"


def test_ingest_dataset_evaluation_experiment_jobs(client: TestClient) -> None:
    ing = client.post(
        "/api/v1/traces/ingest",
        json={"source_name": "ex.jsonl", "content": EXAMPLE_TRACES.read_text()},
        headers=KEY,
    ).json()
    assert ing["quarantined"] == 4 and ing["stored"] > 0
    assert (
        client.get(f"/api/v1/traces/{ing['stored_trajectory_ids'][0]}", headers=KEY).status_code
        == 200
    )
    ds = client.post(
        "/api/v1/datasets/build", json={"name": "api-ds", "level": "d2_schema_valid"}, headers=KEY
    )
    job = wait(client, ds.json()["status_url"])
    assert job["status"] == "SUCCEEDED", job
    dv = job["output"]["dataset_version_id"]
    assert (
        client.get(f"/api/v1/datasets/{dv}", headers=KEY).json()["manifest"]["version"]
        == job["output"]["version"]
    )
    assert (
        "total_records"
        in client.get(f"/api/v1/datasets/{dv}/quality", headers=KEY).json()["quality"]
    )
    ev = client.post(
        "/api/v1/evaluations",
        json={
            "name": "api-eval",
            "splits": ["public_test"],
            "max_tasks_per_family": 1,
            "bootstrap_samples": 200,
        },
        headers=KEY,
    )
    job = wait(client, ev.json()["status_url"])
    assert job["status"] == "SUCCEEDED" and job["output"]["n"] == 3
    assert (
        client.get(f"/api/v1/evaluations/{job['output']['evaluation_id']}", headers=KEY).json()[
            "status"
        ]
        == "completed"
    )
    exp = client.post(
        "/api/v1/experiments", json={"name": "e", "kind": "sft_vs_grpo"}, headers=KEY
    ).json()
    tr = client.post(
        f"/api/v1/experiments/{exp['experiment_id']}/train",
        json={"mode": "cpu-test", "dataset_version_id": dv, "config": {"max_steps": 2}},
        headers=KEY,
    )
    job = wait(client, tr.json()["status_url"])
    assert job["status"] in ("SUCCEEDED", "FAILED")
    detail = client.get(f"/api/v1/experiments/{exp['experiment_id']}", headers=KEY).json()
    assert detail["training_runs"]  # completed or explicitly blocked, never silently missing
    cancel = client.post(f"/api/v1/jobs/{job['job_id']}/cancel", headers=KEY).json()
    assert cancel["status"] == job["status"]  # terminal jobs are unaffected by cancel
    caps = client.get("/api/v1/system/capabilities", headers=KEY).json()
    assert set(caps["training"]["modes"]) == {"demo", "cpu-test", "sft", "grpo"}


def test_openapi_documents_all_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for p in [
        "/api/v1/traces/ingest",
        "/api/v1/datasets/build",
        "/api/v1/rollouts",
        "/api/v1/verifications",
        "/api/v1/experiments/{experiment_id}/train",
        "/api/v1/evaluations",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/system/capabilities",
        "/health/ready",
    ]:
        assert p in paths
