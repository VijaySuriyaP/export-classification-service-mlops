"""Static checks that keep configuration, dashboards and code in step (D3, D4, D5).

None of these need Docker: they catch a renamed metric, a missing env var or a broken Compose file
before anyone runs the stack.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from prometheus_client import REGISTRY

import app.common.metrics  # noqa: F401  (registers the metrics)
from app.common.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _load(path: str):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- dashboard <-> metrics
def _exposed_sample_names() -> set[str]:
    names: set[str] = set()
    for family in REGISTRY.collect():
        if family.type == "counter":
            names.add(family.name + "_total")
        elif family.type == "histogram":
            names.update(family.name + suffix for suffix in ("_bucket", "_sum", "_count"))
        else:
            names.add(family.name)
    return names


def test_every_ecs_metric_in_the_dashboard_exists_in_code():
    dashboard = json.loads((ROOT / "observability/grafana/dashboards/ecs-overview.json").read_text(encoding="utf-8"))
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    used = {name for expr in exprs for name in re.findall(r"\becs_[a-z0-9_]+", expr)}
    assert used, "dashboard references no ecs_ metrics"
    missing = used - _exposed_sample_names()
    assert not missing, f"dashboard queries metrics that no code exposes: {sorted(missing)}"


def test_committed_dashboard_matches_generator():
    spec = importlib.util.spec_from_file_location("build_dashboard", ROOT / "scripts/build_dashboard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = json.loads((ROOT / "observability/grafana/dashboards/ecs-overview.json").read_text(encoding="utf-8"))
    assert committed == module.build(), "run `python scripts/build_dashboard.py` and commit the result"


def test_dashboard_covers_required_signals():
    """D5/A6: p50/p95, error rate, throughput, availability, per-container resources, agent metrics."""
    text = (ROOT / "observability/grafana/dashboards/ecs-overview.json").read_text(encoding="utf-8")
    for needle in ("histogram_quantile(0.50", "histogram_quantile(0.95", "status='failed'", "ecs_runs_finished_total",
                   "up", "process_cpu_seconds_total", "process_resident_memory_bytes", "process_start_time_seconds",
                   "ecs_agent_steps_total", "ecs_run_tool_calls", "ecs_llm_retries_total",
                   "ecs_circuit_breaker_state", "ecs_llm_cost_usd_total"):
        assert needle in text, f"dashboard lacks {needle}"


# ---------------------------------------------------------------- .env.example
def _env_keys() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_env_example_documents_every_setting():
    keys = _env_keys()
    set_in_compose = {"SERVICE_NAME"}
    missing = {name.upper() for name in Settings.model_fields} - set(keys) - set_in_compose
    assert not missing, f".env.example lacks: {sorted(missing)}"


def test_env_example_has_no_credentials():
    for key, value in _env_keys().items():
        if re.search(r"(API_KEY|TOKEN|SECRET)$", key):
            assert value == "", f"{key} must be empty in .env.example"
    assert "change-me" in _env_keys()["GF_SECURITY_ADMIN_PASSWORD"]


def test_env_example_values_are_valid_settings(monkeypatch):
    for key, value in _env_keys().items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    assert settings.redis_url.startswith("redis://")
    assert settings.agent_runner == "app.agents.stub_runner:StubAgentRunner"


def test_real_env_file_is_ignored_by_git():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore and "!.env.example" in ignore


# ---------------------------------------------------------------- compose
def test_compose_has_required_services_and_dependencies():
    compose = _load("compose.yaml")
    services = compose["services"]
    assert len([s for s, c in services.items() if "profiles" not in c]) >= 4          # D3
    for name in ("api", "agent-worker", "redis", "llm-stub", "prometheus", "grafana", "jaeger", "web"):
        assert name in services, name
    assert services["api"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["agent-worker"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert "healthcheck" in services["redis"]


def test_agent_worker_is_scalable():
    """A7: no fixed container name and no published host ports, so --scale agent-worker=3 works."""
    worker = _load("compose.yaml")["services"]["agent-worker"]
    assert "container_name" not in worker and "ports" not in worker


def test_images_are_pinned_and_secrets_come_from_env_file():
    compose = _load("compose.yaml")
    for name, service in compose["services"].items():
        image = service.get("image")
        if image:
            assert ":" in image and not image.endswith(":latest"), f"{name} image is not pinned: {image}"
    for name in ("api", "agent-worker", "llm-stub"):
        assert compose["services"][name]["env_file"] == [".env"]
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert not re.search(r"(?i)(api_key|password|secret|token)\s*:\s*[^\s\"$]", text.replace("GF_AUTH", ""))


@pytest.mark.parametrize("dockerfile", ["api", "worker", "llm-stub", "web"])
def test_dockerfiles_define_healthcheck_and_run_unprivileged(dockerfile):
    text = (ROOT / f"docker/{dockerfile}.Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text and re.search(r"^USER\s+\S+", text, re.M)


def test_dockerfile_copies_match_repo_layout():
    for dockerfile in ("api", "worker", "llm-stub", "web"):
        for line in re.findall(r"^COPY\s+(.+)$", (ROOT / f"docker/{dockerfile}.Dockerfile").read_text(encoding="utf-8"), re.M):
            for src in line.split()[:-1]:                       # every source, not just the first
                assert (ROOT / src.rstrip("/")).exists(), f"{dockerfile}: COPY source {src} missing"


# ---------------------------------------------------------------- web client
def test_web_client_is_wired_to_the_api():
    """A8: the web service depends on a healthy api, and nginx proxies /api/ to the api service port."""
    compose = _load("compose.yaml")
    web = compose["services"]["web"]
    assert web["depends_on"]["api"]["condition"] == "service_healthy"
    assert any(str(p).endswith(":8080") for p in web["ports"])
    nginx = (ROOT / "web/nginx.conf").read_text(encoding="utf-8")
    assert "location /api/" in nginx and "http://api:8000" in nginx and "location = /healthz" in nginx


def test_web_client_only_calls_existing_api_routes():
    """Every path the browser client calls must exist in the API, so a renamed route breaks CI, not the demo."""
    from app.api.main import create_app
    import fakeredis

    routes = {getattr(r, "path", "") for r in create_app(Settings(redis_url="redis://x"), fakeredis.FakeRedis()).routes}
    js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    called = set(re.findall(r'api\("(?:GET|POST)",\s*[`"]([^`"?]+)', js))
    assert called, "found no api() calls in web/app.js"
    normalised = {re.sub(r"\$\{[^}]+\}", "{run_id}", c) for c in called}
    missing = normalised - routes
    assert not missing, f"web client calls routes the API does not have: {sorted(missing)}"


def test_web_client_never_renders_server_text_as_html():
    js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_requirements_files_resolve():
    for line in (ROOT / "requirements/dev.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("-r "):
            assert (ROOT / "requirements" / line[3:].strip()).exists()


# ---------------------------------------------------------------- prometheus
def test_prometheus_scrapes_every_service_and_discovers_scaled_workers():
    config = _load("observability/prometheus/prometheus.yml")
    jobs = {j["job_name"]: j for j in config["scrape_configs"]}
    assert {"api", "agent-worker", "llm-stub", "redis"} <= set(jobs)
    assert jobs["agent-worker"]["dns_sd_configs"][0]["names"] == ["agent-worker"]
    assert jobs["agent-worker"]["dns_sd_configs"][0]["port"] == Settings().worker_metrics_port
    assert "api:8000" in jobs["api"]["static_configs"][0]["targets"]
