"""Offline installed-Desktop contract tests; no Hermes/model requests."""
import importlib
import tempfile
import unittest
from pathlib import Path


class FixtureRPC:
    def __init__(self):
        self.calls = []
        self.frames = []
        self.replay = {"events": [], "latest_seq": 0, "truncated": False, "epoch": "one"}

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "ping":
            return {"pong": True}
        if method == "session.create":
            return {"session_id": "runtime_one", "stored_session_id": "durable_one"}
        if method == "prompt.submit":
            return {"status": "streaming", "user_row_id": 11}
        if method == "session.events.since":
            return dict(self.replay)
        if method == "session.interrupt":
            return {"status": "interrupted"}
        raise AssertionError("unexpected RPC: " + method)

    def drain(self):
        frames, self.frames = self.frames, []
        return frames

    def event(self, kind, payload):
        self.frames.append({"jsonrpc": "2.0", "method": "event", "params": {
            "session_id": "runtime_one", "type": kind, "payload": payload}})


class DesktopContractTests(unittest.TestCase):
    def adapter(self, rpc, path=":memory:"):
        try:
            module = importlib.import_module("pocket.desktop")
        except ModuleNotFoundError:
            self.fail("The installed Desktop contract has no implementation yet")
        adapter = module.DesktopAdapter(rpc, path)
        self.addCleanup(adapter.close)
        adapter.check()
        return adapter

    def test_acknowledgement_is_not_completion_and_stored_identity_is_retained(self):
        rpc = FixtureRPC()
        adapter = self.adapter(rpc)
        run = adapter.create("hello", "device:request", history=[{"role": "user", "content": "earlier"}])
        self.assertEqual(adapter.read(run)["state"], "running")
        create = next(p for m, p in rpc.calls if m == "session.create")
        self.assertEqual(create["messages"], [{"role": "user", "content": "earlier"}])
        rpc.event("message.complete", {"text": "answer", "status": "complete"})
        self.assertEqual(adapter.read(run), {"state": "completed", "result": "answer"})
        self.assertEqual(adapter.handoff(run)["stored_session_id"], "durable_one")

    def test_duplicate_and_restart_never_submit_prompt_again(self):
        rpc = FixtureRPC()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "desktop.sqlite")
            adapter = self.adapter(rpc, path)
            run = adapter.create("hello", "device:request")
            self.assertEqual(adapter.create("hello", "device:request"), run)
            another = self.adapter(rpc, path)
            self.assertEqual(another.create("hello", "device:request"), run)
            self.assertEqual(sum(m == "prompt.submit" for m, _ in rpc.calls), 1)
            another.close()
            adapter.close()

    def test_interactive_approval_is_handed_off_without_replying(self):
        rpc = FixtureRPC()
        adapter = self.adapter(rpc)
        run = adapter.create("hello", "device:request")
        rpc.frames.append({"jsonrpc": "2.0", "id": "approval-1", "method": "approval",
                           "params": {"session_id": "runtime_one", "command": "fixture"}})
        self.assertEqual(adapter.read(run)["state"], "awaiting_approval")
        self.assertEqual(adapter.handoff(run)["interface"], "hermes_desktop")
        self.assertFalse(any(m.startswith("approval") for m, _ in rpc.calls))

    def test_replay_gap_and_stop_acknowledgement_do_not_invent_outcomes(self):
        rpc = FixtureRPC()
        adapter = self.adapter(rpc)
        run = adapter.create("hello", "device:request")
        adapter.stop(run)
        self.assertEqual(adapter.read(run)["state"], "stopping")
        rpc.replay["truncated"] = True
        self.assertEqual(adapter.read(run)["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
