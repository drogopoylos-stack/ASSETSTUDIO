"""Offline regression checks; no credentials, browser or inference calls."""
import queue
import unittest
from unittest.mock import Mock, patch

from asset_studio import agents, codex_account as account, terminal


class CodexTests(unittest.TestCase):
    def test_catalog_paginates_and_deduplicates_including_hidden(self):
        client = Mock()
        client.call.side_effect = [
            {"data": [{"model": "one"}], "nextCursor": "page2"},
            {"data": [{"model": "one"}, {"id": "two", "hidden": True}], "nextCursor": None},
        ]
        with patch.object(account, "client", return_value=client):
            result = account.models()
        self.assertEqual([m["model"] for m in result["models"]], ["one", "two"])
        self.assertFalse(result["access_verified"])
        self.assertTrue(client.call.call_args.args[1]["includeHidden"])
        self.assertEqual(client.call.call_args.args[1]["cursor"], "page2")

    def test_catalog_rejects_cursor_cycle(self):
        client = Mock()
        client.call.side_effect = [{"nextCursor": "a"}, {"nextCursor": "b"}, {"nextCursor": "a"}]
        with patch.object(account, "client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "invalid model page"):
                account.models()

    def fake_client(self, status):
        client = Mock()
        client.call.side_effect = [
            {"account": {"type": "chatgpt"}},
            {"thread": {"id": "thread"}, "model": "selected"},
            {"turn": {"id": "turn"}},
        ]
        client.events = queue.Queue()
        client.events.put({"method": "turn/completed", "params": {"turn": {"id": "other", "status": "completed"}}})
        client.events.put({"method": "turn/completed", "params": {"turn": {"id": "turn", "status": status, "error": {"message": "Quota exceeded"}}}})
        return client

    def test_only_completed_inference_is_success(self):
        for status in ("completed", "failed", "interrupted"):
            with self.subTest(status=status):
                client = self.fake_client(status)
                with patch.object(account, "Client", return_value=client):
                    if status == "completed":
                        self.assertTrue(account.test_connection("selected")["ok"])
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Quota exceeded"):
                            account.test_connection("selected")
                client.close.assert_called_once()
                params = client.call.call_args_list[1].args[1]
                self.assertEqual(params["model"], "selected")
                self.assertEqual(params["sandbox"], "read-only")
                self.assertEqual(params["approvalPolicy"], "never")

    def test_old_login_completion_does_not_override_new_login(self):
        client = Mock()
        client.events = queue.Queue()
        client.events.put({"method": "account/login/completed", "params": {"loginId": "old", "success": False}})
        client.call.return_value = {"account": None}
        with patch.object(account, "client", return_value=client), patch.object(account, "_login", {"loginId": "new"}):
            self.assertEqual(account.status()["login"], {"loginId": "new"})

    def test_repeated_login_reuses_pending_flow(self):
        client = Mock()
        with patch.object(account, "client", return_value=client), patch.object(account, "_login", {"loginId": "new", "authUrl": "https://example.test"}):
            self.assertEqual(account.login()["loginId"], "new")
            client.call.assert_not_called()
            account.cancel_login()
            client.call.assert_called_once_with("account/login/cancel", {"loginId": "new"})

    def test_terminal_passes_model_and_full_effort(self):
        with patch.object(terminal, "_resolve", return_value="codex.exe"), patch.object(terminal, "create", return_value={"ok": True}) as create:
            terminal.launch("codex", model="gpt-example", effort="xhigh")
            args = create.call_args.args[0]
            self.assertIn("gpt-example", args)
            self.assertIn('model_reasoning_effort="xhigh"', args)
            create.reset_mock()
            self.assertFalse(terminal.launch("codex", model="bad&command")["ok"])
            create.assert_not_called()
        self.assertEqual(agents._codex_effort("max"), "max")


if __name__ == "__main__":
    unittest.main()
