from enterprise_ai_worker.main import WorkerResult, run_once


def test_worker_returns_explicit_idle_state() -> None:
    assert run_once() is WorkerResult.IDLE

