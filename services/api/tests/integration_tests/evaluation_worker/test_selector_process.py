"""Physical fixed Python children on SelectorEventLoop; no DB, target, provider or Agent."""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_agent_evaluation_worker_protocol import definition, result

from app import agent_evaluation_platform_worker as worker


def selector(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


def setup_driver(tmp_path, monkeypatch, program):
    """Only process lifecycle is under test; runtime/facts are explicit protocol substitutes."""
    row = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "repeats": 1,
        "snapshot": {"definition": definition(), "version_set": {}},
        "budgets": {"max_seconds": 300, "max_model_calls": 20, "max_tokens": 20000, "max_sandboxes": 2},
    }
    config = worker.DriverConfig(tmp_path, Path(sys.executable), "a" * 40, {}, tmp_path / "admin")
    driver = worker.FixedSuiteDriver(config, {})
    monkeypatch.setattr(driver, "verify_source", lambda: True)
    monkeypatch.setattr(driver, "validate_definition", lambda *args: True)
    monkeypatch.setattr(driver, "validate_runtime_proof", lambda proof, nonce: proof["nonce"] == nonce)
    entry = tmp_path / "fixed-process-fixture.py"
    entry.write_text(program, encoding="utf-8")
    monkeypatch.setattr(driver, "process_command", lambda: [sys.executable, "-I", str(entry)])
    permit = SimpleNamespace(origin="http://127.0.0.1:23456", secret="s" * 40)
    directory = tmp_path / ".local/agent-evaluations/platform-worker" / str(row["id"])
    return row, driver, permit, directory


async def not_cancelled():
    return False


def test_physical_selector_child_file_stdin_mac_and_nonce(tmp_path, monkeypatch):
    row, driver, permit, directory = setup_driver(tmp_path, monkeypatch, "")
    response = result(row)
    program = (
        "import sys,json,os,hmac,hashlib; from pathlib import Path\n"
        "envelope=json.load(sys.stdin)\n"
        f"directory=Path({str(directory)!r})\n"
        "(directory/'stdin-witness.json').write_text(json.dumps({'seekable':sys.stdin.seekable()}))\n"
        "proof=json.dumps({'nonce':envelope['nonce']}).encode()\n"
        "(directory/'runtime-attestation.json').write_bytes(proof)\n"
        f"result=json.loads({json.dumps(response)!r})\n"
        "result['runtime_attestation_sha256']=hashlib.sha256(proof).hexdigest()\n"
        "raw=json.dumps(result).encode(); (directory/'result.json').write_bytes(raw)\n"
        "receipt={'schema_version':1,'run_id':result['run_id'],'owner_id':result['owner_id'],"
        "'sha256':hashlib.sha256(raw).hexdigest(),'mac':hmac.new("
        "os.environ['EVALUATION_PLATFORM_RESULT_SECRET'].encode(),raw,hashlib.sha256).hexdigest()}\n"
        "(directory/'result-ready.json').write_text(json.dumps(receipt))\n"
    )
    (tmp_path / "fixed-process-fixture.py").write_text(program, encoding="utf-8")
    actual = selector(driver.execute(row, permit, not_cancelled))
    assert actual["samples"][0]["task_outcome"] == "FAIL"  # The same synthetic factual FAIL survives import parsing.
    assert json.loads((directory / "stdin-witness.json").read_text()) == {"seekable": True}
    envelope = (directory / "runtime-envelope.json").read_text()
    assert permit.secret not in envelope and "EVALUATION_PLATFORM_RESULT_SECRET" not in envelope
    assert json.loads((directory / "process.json").read_text())["status"] == "STARTED"
    assert not (directory / "retained-process.json").exists()


def test_nonreading_physical_child_cannot_block_before_supervision(tmp_path, monkeypatch):
    row, driver, permit, directory = setup_driver(tmp_path, monkeypatch, "import time; time.sleep(2.5)\n")
    original = driver.runtime_envelope
    monkeypatch.setattr(driver, "runtime_envelope", lambda *args: {**original(*args), "argument": "x" * 200_000})
    observed = []

    async def cancelled():
        observed.append("supervised while child is alive")
        return True

    with pytest.raises(FileNotFoundError):
        selector(driver.execute(row, permit, cancelled))
    assert observed and (directory / "suite/cancel.request").exists()
    assert (directory / "runtime-envelope.json").stat().st_size > 131_072
    with pytest.raises(FileExistsError):
        selector(driver.execute(row, permit, not_cancelled))


def test_repeated_cancellation_during_launch_preserves_exact_physical_handle(tmp_path, monkeypatch):
    row, driver, permit, directory = setup_driver(tmp_path, monkeypatch, "import time; time.sleep(0.3)\n")
    entered, release = threading.Event(), threading.Event()
    original = worker.Popen
    children = []

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Controlled launch gate was not released")
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(worker, "Popen", delayed)

    async def probe():
        pending = asyncio.create_task(driver.execute(row, permit, not_cancelled))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            pending.cancel()
            await asyncio.sleep(0.05)
            pending.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert len(children) == 1
            retained = json.loads((directory / "retained-process.json").read_text())
            assert retained["pid"] == children[0].pid and retained["status"] == "RETAINED"
            assert (directory / "suite/cancel.request").exists()
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            for child in children:
                assert await asyncio.to_thread(child.wait, 5) == 0  # Only this fixed 0.3s fixture child is waited.

    selector(probe())


def test_cancel_all_tasks_during_launch_keeps_handle_and_executor_finishes(tmp_path, monkeypatch):
    row, driver, permit, directory = setup_driver(tmp_path, monkeypatch, "import time; time.sleep(1)\n")
    entered, release = threading.Event(), threading.Event()
    original = worker.Popen
    children = []

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Controlled launch gate was not released")
        process = original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(worker, "Popen", delayed)

    async def probe():
        pending = asyncio.create_task(driver.execute(row, permit, not_cancelled))
        timer = threading.Timer(0.2, release.set)
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            timer.start()  # Independent of the event loop whose pending tasks are all cancelled.
            current = asyncio.current_task()
            cancelled = [task for task in asyncio.all_tasks() if task is not current]
            assert pending in cancelled
            for task in cancelled:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert len(children) == 1
            retained = json.loads((directory / "retained-process.json").read_text())
            assert retained["pid"] == children[0].pid and retained["status"] == "RETAINED"
            assert json.loads((directory / "process.json").read_text())["pid"] == children[0].pid
            assert (directory / "suite/cancel.request").exists()
        finally:
            release.set()
            timer.cancel()
            if timer.ident is not None:
                timer.join(timeout=5)
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            for child in children:
                assert await asyncio.to_thread(child.wait, 5) == 0

    selector(probe())
    deadline = time.monotonic() + 5
    while any(thread.name.startswith("evaluation-launch") for thread in threading.enumerate()):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_creation_error_closes_executor_without_replay_or_false_clean(tmp_path, monkeypatch):
    row, driver, permit, directory = setup_driver(tmp_path, monkeypatch, "pass\n")

    def failed(*args, **kwargs):
        raise OSError("Controlled creation failure; no fixture child started")

    monkeypatch.setattr(worker, "Popen", failed)
    with pytest.raises(OSError, match="Controlled creation failure"):
        selector(driver.execute(row, permit, not_cancelled))
    assert json.loads((directory / "process.json").read_text())["status"] == "LAUNCHING"
    assert not (directory / "result-ready.json").exists()
    with pytest.raises(FileExistsError):
        selector(driver.execute(row, permit, not_cancelled))
    deadline = time.monotonic() + 5
    while any(thread.name.startswith("evaluation-launch") for thread in threading.enumerate()):
        assert time.monotonic() < deadline
        time.sleep(0.01)
