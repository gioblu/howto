import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import howto


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._data).encode("utf-8")


class HowtoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        os.environ["HOME"] = self.temp_dir.name
        howto.ensure_storage()

    def capture_output(self, func, *args, **kwargs):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            result = func(*args, **kwargs)
        return stdout.getvalue(), stderr.getvalue(), result

    def test_config_sets_provider_via_main(self) -> None:
        code = howto.main(["config", "127.0.0.1:11434"])
        config = howto.load_config()
        self.assertEqual(code, 0)
        self.assertEqual(config["provider"], "http://127.0.0.1:11434")

    def test_list_prints_models(self) -> None:
        provider_data = {
            "object": "list",
            "data": [{"id": "llama2", "object": "model"}],
        }
        with mock.patch.object(howto.urllib.request, "urlopen", return_value=FakeResponse(provider_data)):
            stdout, stderr, code = self.capture_output(howto.main, ["list"])
        self.assertEqual(code, 0)
        self.assertIn("llama2", stdout)
        self.assertEqual(stderr, "")

    def test_set_changes_model_via_main(self) -> None:
        code = howto.main(["set", "custom-model"])
        config = howto.load_config()
        self.assertEqual(code, 0)
        self.assertEqual(config["model"], "custom-model")

    def test_model_alias_changes_model_via_main(self) -> None:
        code = howto.main(["model", "mistral:7b"])
        config = howto.load_config()
        self.assertEqual(code, 0)
        self.assertEqual(config["model"], "mistral:7b")

    def test_mode_changes_mode_via_main(self) -> None:
        code = howto.main(["mode", "yolo"])
        config = howto.load_config()
        self.assertEqual(code, 0)
        self.assertEqual(config["mode"], "yolo")

    def test_alias_create_and_use_via_main(self) -> None:
        code = howto.main(["alias", "sayhi", "echo", "hi"])
        self.assertEqual(code, 0)
        config = howto.load_config()
        self.assertEqual(config["aliases"]["sayhi"], "echo hi")

        with mock.patch.object(howto, "post_prompt", return_value="echo hi") as post_prompt:
            def confirm_and_print(command: str, mode: str) -> bool:
                print(command)
                return True

            with mock.patch.object(howto, "ask_user_permission", side_effect=confirm_and_print):
                with mock.patch.object(howto, "ask_to_cache"):
                    with mock.patch.object(howto.subprocess, "run", return_value=mock.Mock(returncode=0)) as run_cmd:
                        stdout, stderr, code = self.capture_output(howto.main, ["sayhi"])
        self.assertEqual(code, 0)
        self.assertIn("echo hi", stdout)
        run_cmd.assert_called_once()
        post_prompt.assert_called_once()

    def test_raw_returns_provider_json(self) -> None:
        with mock.patch.object(howto, "post_prompt", return_value='{ "answer": "ok" }') as prompt:
            stdout, stderr, code = self.capture_output(howto.main, ["raw", "ls"])
        self.assertEqual(code, 0)
        self.assertIn("{ \"answer\": \"ok\" }", stdout)
        self.assertEqual(stderr, "")
        prompt.assert_called_once()

    def test_repl_sends_history(self) -> None:
        with mock.patch("builtins.input", side_effect=["ls -la", "no", "exit"]):
            with mock.patch.object(howto, "post_prompt", return_value="echo ls"):
                stdout, stderr, _ = self.capture_output(howto.repl_loop)
        self.assertIn("Entering howto REPL", stdout)
        self.assertIn("echo ls", stdout)
        self.assertEqual(stderr, "")

    def test_repl_prompt_includes_history_in_order(self) -> None:
        with mock.patch("builtins.input", side_effect=["first task", "y", "second task", "y", "exit"]):
            with mock.patch.object(howto, "post_prompt", side_effect=["echo first", "echo second"]) as post_prompt:
                first = mock.Mock(returncode=0, stdout="", stderr="")
                second = mock.Mock(returncode=0, stdout="", stderr="")
                with mock.patch.object(howto.subprocess, "run", side_effect=[first, second]):
                    self.capture_output(howto.repl_loop)

        self.assertEqual(post_prompt.call_count, 2)
        first_prompt = post_prompt.call_args_list[0].args[2]
        second_prompt = post_prompt.call_args_list[1].args[2]

        self.assertTrue(first_prompt.startswith(
            howto.PREPROMPT + "\nYou are now in the directory "
        ))
        self.assertIn("first task", first_prompt)
        self.assertNotIn("second task", first_prompt)

        self.assertIn("first task\nCommand: echo first\nsecond task", second_prompt)
        self.assertTrue(second_prompt.startswith(
            howto.PREPROMPT + "\nYou are now in the directory "
        ))

    def test_normalize_multiline_command_preserves_heredoc(self) -> None:
        text = (
            'cat > "$file" <<EOF\n'
            'line1\n'
            'line2\n'
            'EOF\n'
            'echo done'
        )
        result = howto.normalize_multiline_command(text)
        self.assertEqual(
            result,
            'cat > "$file" <<EOF\nline1\nline2\nEOF && echo done'
        )

    def test_repl_prints_command_output_on_success(self) -> None:
        with mock.patch("builtins.input", side_effect=["do it", "y", "exit"]):
            with mock.patch.object(howto, "post_prompt", return_value="echo hello"):
                success = mock.Mock(returncode=0, stdout="hello\n", stderr="")
                with mock.patch.object(howto.subprocess, "run", return_value=success):
                    stdout, stderr, _ = self.capture_output(howto.repl_loop)
        self.assertIn("hello", stdout)
        self.assertEqual(stderr, "")

    def test_repl_persists_cd_across_commands(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        old_cwd = os.getcwd()
        os.chdir(temp_dir.name)
        self.addCleanup(lambda: os.chdir(old_cwd))
        repl_dir = Path(temp_dir.name) / "repl"
        repl_dir.mkdir()

        with mock.patch("builtins.input", side_effect=["go to repl", "y", "list it", "y", "exit"]):
            with mock.patch.object(howto, "post_prompt", side_effect=["cd repl", "pwd"]) as post_prompt:
                first = mock.Mock(returncode=0, stdout="", stderr="")
                second = mock.Mock(returncode=0, stdout=str(repl_dir) + "\n", stderr="")
                with mock.patch.object(howto.subprocess, "run", side_effect=[first, second]) as run_cmd:
                    stdout, stderr, _ = self.capture_output(howto.repl_loop)
        self.assertEqual(post_prompt.call_count, 2)
        self.assertEqual(run_cmd.call_args_list[1].kwargs["cwd"], str(repl_dir))
        self.assertIn(str(repl_dir), stdout)
        self.assertEqual(stderr, "")

    def test_repl_continues_after_command_failure(self) -> None:
        with mock.patch("builtins.input", side_effect=["do it", "y", "y", "exit"]):
            with mock.patch.object(howto, "post_prompt", side_effect=["echo fail", "echo fix"]) as post_prompt:
                failed = mock.Mock(returncode=1, stdout="bad output\n", stderr="error occurred\n")
                success = mock.Mock(returncode=0, stdout="", stderr="")
                with mock.patch.object(howto.subprocess, "run", side_effect=[failed, success]):
                    stdout, stderr, _ = self.capture_output(howto.repl_loop)
        self.assertIn("Command failed with exit code 1.", stderr)
        self.assertIn("Entering howto REPL", stdout)
        self.assertIn("echo fix", stdout)
        self.assertEqual(post_prompt.call_count, 2)

    def test_fix_history_from_file(self) -> None:
        history_file = Path(self.temp_dir.name) / "history.txt"
        history_file.write_text("ls: command not found\n")
        with mock.patch.object(howto, "post_prompt", return_value="echo fixed") as prompt:
            stdout, stderr, code = self.capture_output(howto.main, ["fix", str(history_file)])
        self.assertEqual(code, 0)
        self.assertIn("echo fixed", stdout)
        self.assertEqual(stderr, "")
        prompt.assert_called_once()

    def test_save_manual_with_response_flag(self) -> None:
        code = howto.main(["save", "hello", "--response", "value"])
        self.assertEqual(code, 0)
        cache = howto.load_cache()
        self.assertEqual(cache["hello"], "value")

    def test_save_manual_uses_cached_response(self) -> None:
        howto.cache_response("hello", "world")
        code = howto.main(["save", "hello"])
        self.assertEqual(code, 0)
        cache = howto.load_cache()
        self.assertEqual(cache["hello"], "world")

    def test_prompt_cached_uses_cache_without_provider_call(self) -> None:
        howto.cache_response("cached prompt", "saved response")
        with mock.patch.object(howto, "post_prompt") as post_prompt:
            with mock.patch.object(howto, "ask_user_permission", return_value=False):
                stdout, stderr, code = self.capture_output(howto.main, ["cached", "prompt"])
        self.assertEqual(code, 0)
        post_prompt.assert_not_called()
        self.assertEqual(stderr, "")

    def test_cached_prompt_still_asks_permission_and_executes(self) -> None:
        howto.cache_response("create file test.txt", "touch test.txt")
        with mock.patch.object(howto, "post_prompt") as post_prompt:
            with mock.patch.object(howto, "ask_user_permission", return_value=True):
                with mock.patch.object(howto, "ask_to_cache"):
                    with mock.patch.object(howto.subprocess, "run", return_value=mock.Mock(returncode=0)) as run_cmd:
                        stdout, stderr, code = self.capture_output(howto.main, ["create", "file", "test.txt"])
        self.assertEqual(code, 0)
        post_prompt.assert_not_called()
        run_cmd.assert_called_once()
        self.assertEqual(stderr, "")

    def test_verbose_model_response_is_sanitized_to_command(self) -> None:
        verbose = (
            "To create a new file named `test.txt`, run:\n\n```bash\ntouch test.txt\n```"
        )
        with mock.patch.object(howto, "post_prompt", return_value=verbose):
            def confirm(command: str, mode: str) -> bool:
                print(command)
                return False

            with mock.patch.object(howto, "ask_user_permission", side_effect=confirm):
                with mock.patch.object(howto, "ask_to_cache"):
                    stdout, stderr, code = self.capture_output(howto.main, ["create", "the", "file", "test.txt"])
        self.assertEqual(code, 0)
        self.assertIn("touch test.txt", stdout)
        self.assertNotIn("To create a new file", stdout)
        self.assertEqual(stderr, "")

    def test_ask_user_permission_accepts_yes(self) -> None:
        self.assertTrue(howto.ask_user_permission("echo hi", mode="yolo"))


if __name__ == "__main__":
    unittest.main()
