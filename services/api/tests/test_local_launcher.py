"""Launcher tests use fake processes; never start/stop the user's running services."""

import argparse
import importlib.util
import json
import subprocess
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "local_launcher", Path(__file__).resolve().parents[3] / "scripts/local_ai.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    for name in (".venv/bin/python", "apps/web/out/index.html", "work/ollama-runtime/ollama"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.chmod(0o700)
    monkeypatch.setattr(launcher.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    return tmp_path


@pytest.mark.parametrize("value", ["0", "65536", "-1", "hello"])
def test_invalid_ports_are_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        launcher.port_number(value)


def test_preflight_does_not_create_database_or_keys(installation):
    assert all(ok for ok, _ in launcher.preflight())
    assert not (installation / "data").exists()


def test_web_launcher_never_inherits_cloud_migration_target(installation, monkeypatch):
    monkeypatch.chdir(installation)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://external/production")
    monkeypatch.setattr(launcher.sys, "argv", ["local_ai.py", "web"])
    execute = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(launcher.os, "execve", execute)
    with pytest.raises(SystemExit):
        launcher.main()
    env = execute.call_args.args[2]
    assert env["MIGRATION_DATABASE_URL"] == env["DATABASE_URL"]
    assert env["DATABASE_URL"].startswith("sqlite:///")


@pytest.mark.parametrize("existing", ["enterprise.db", "uploads/document/payload.enc"])
def test_existing_data_without_key_is_blocked(installation, existing):
    path = installation / "data/local/ollama" / existing
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-plaintext")
    assert not all(ok for ok, _ in launcher.preflight())
    assert not (installation / "data/local/ollama/master.key").exists()


def test_invalid_key_is_not_replaced_or_printed(installation, capsys, monkeypatch):
    key = installation / "data/local/ollama/master.key"
    key.parent.mkdir(parents=True)
    key.write_text("bad-key-secret")
    monkeypatch.setattr(launcher, "local_json", Mock(side_effect=OSError))
    monkeypatch.setattr(launcher.shutil, "which", lambda _: None)
    assert launcher.doctor(11435) == 1
    assert "bad-key-secret" not in capsys.readouterr().out
    assert key.read_text() == "bad-key-secret"


def test_model_names_and_malformed_responses():
    assert (
        launcher.missing_models({"models": [{"name": "qwen3:4b"}, {"name": "bge-m3:latest"}]}) == []
    )
    assert launcher.missing_models({"models": []}) == list(launcher.MODELS)
    with pytest.raises(ValueError):
        launcher.missing_models({"models": "not-a-list"})


def test_local_probe_disables_proxy_and_redirects(monkeypatch):
    opener = Mock()
    opener.open.return_value = BytesIO(json.dumps({"models": []}).encode())
    builder = Mock(return_value=opener)
    monkeypatch.setattr(launcher, "build_opener", builder)
    assert launcher.local_json(11435, "/api/tags") == {"models": []}
    assert builder.call_args.args[0].proxies == {}
    assert isinstance(builder.call_args.args[1], launcher.NoRedirect)
    assert (
        builder.call_args.args[1].redirect_request(
            None, None, 302, None, {}, "https://outside.test"
        )
        is None
    )
    assert opener.open.call_args.args[0] == "http://127.0.0.1:11435/api/tags"


def test_local_probe_bounds_response_size(monkeypatch):
    opener = Mock()
    opener.open.return_value = BytesIO(b" " * (1024 * 1024 + 1))
    monkeypatch.setattr(launcher, "build_opener", lambda *_: opener)
    with pytest.raises(ValueError, match="过大"):
        launcher.local_json(11435, "/api/tags")


@pytest.fixture
def start_context(monkeypatch):
    monkeypatch.setattr(launcher, "preflight", lambda: [(True, "ok")])
    monkeypatch.setattr(launcher, "port_available", lambda _: True)
    monkeypatch.setattr(launcher.signal, "signal", Mock())
    model = Mock()
    web = Mock()
    model.poll.return_value = web.poll.return_value = None
    worker = Mock()
    worker.poll.return_value = None
    spawn = Mock(side_effect=[model, web, worker])
    spawn.worker = worker
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        launcher,
        "wait_ready",
        Mock(return_value={"models": [{"name": "qwen3:4b"}, {"name": "bge-m3:latest"}]}),
    )
    args = argparse.Namespace(port=8201, model_port=11435, cpu=True)
    return args, model, web, spawn


def test_port_conflict_never_starts_or_stops_processes(start_context, monkeypatch):
    args, model, web, spawn = start_context
    monkeypatch.setattr(launcher, "port_available", lambda _: False)
    assert launcher.start(args) == 1
    spawn.assert_not_called()
    model.terminate.assert_not_called()
    web.terminate.assert_not_called()


def test_equal_ports_rejected(start_context):
    args, _, _, spawn = start_context
    args.port = args.model_port
    assert launcher.start(args) == 1
    spawn.assert_not_called()


def test_missing_models_stop_owned_model_without_starting_web(start_context, monkeypatch):
    args, model, web, spawn = start_context
    monkeypatch.setattr(launcher, "wait_ready", lambda *_: {"models": []})
    assert launcher.start(args) == 1
    assert spawn.call_count == 1
    model.terminate.assert_called_once()
    web.terminate.assert_not_called()


def test_web_spawn_failure_cleans_up_model(start_context):
    args, model, _, spawn = start_context
    spawn.side_effect = [model, OSError("cannot start")]
    assert launcher.start(args) == 1
    model.terminate.assert_called_once()


def test_ctrl_c_stops_both_owned_services_and_forwards_ports(start_context, monkeypatch):
    args, model, web, spawn = start_context
    args.port = 8202
    args.model_port = 11436
    monkeypatch.setattr(launcher.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    assert launcher.start(args) == 0
    model.terminate.assert_called_once()
    web.terminate.assert_called_once()
    spawn.worker.terminate.assert_called_once()
    assert "--async-processing" in spawn.call_args_list[1].args[0]
    assert spawn.call_args_list[2].args[0][2] == "worker"
    assert spawn.call_args_list[0].args[0][-3:] == ["--model-port", "11436", "--cpu"]
    assert spawn.call_args_list[1].args[0][-4:] == ["--port", "8202", "--model-port", "11436"]


def test_crashed_child_stops_sibling(start_context):
    args, model, web, _ = start_context
    web.poll.return_value = 1
    assert launcher.start(args) == 1
    model.terminate.assert_called_once()
    web.terminate.assert_not_called()


def test_wait_ready_rejects_crashed_process():
    process = Mock()
    process.poll.return_value = 1
    with pytest.raises(RuntimeError, match="提前退出"):
        launcher.wait_ready(process, 8201, "/readyz")


def test_readiness_requires_database_and_service_identity(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    probe = Mock(
        side_effect=[
            {"status": "ok"},
            {"status": "ready", "service": "enterprise-ai-api", "database": "ok"},
        ]
    )
    monkeypatch.setattr(launcher, "local_json", probe)
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    assert launcher.wait_ready(process, 8201, "/readyz")["database"] == "ok"
    assert probe.call_count == 2


def test_shutdown_escalates_only_for_owned_unresponsive_child():
    child = Mock()
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("child", 8), 0]
    launcher.stop_owned_processes([child])
    child.terminate.assert_called_once()
    child.kill.assert_called_once()
