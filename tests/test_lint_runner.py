"""Regression tests for lint file coverage and failure detection."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("project_lint", ROOT / "tests/lint.py")
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


class LintRunnerTests(unittest.TestCase):
    def test_inventory_includes_untracked_files_and_omits_deleted_and_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            (root / ".gitignore").write_text(".env\n.lint-venv/\n")
            (root / "tracked.sh").write_text("#!/bin/sh\ntrue\n")
            (root / "deleted.sh").write_text("#!/bin/sh\ntrue\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            (root / "deleted.sh").unlink()
            (root / "new file.py").write_text("pass\n")
            (root / ".env").write_text("IGNORED=test\n")
            (root / ".lint-venv").mkdir()
            (root / ".lint-venv/dependency.py").write_text("broken dependency\n")
            with patch.object(LINT, "ROOT", root):
                files = LINT.project_files()
            self.assertEqual(files, [".gitignore", "new file.py", "tracked.sh"])

    def test_text_hygiene_detects_each_common_error(self):
        cases = {
            "merge.py": b"<<<<<<< ours\n",
            "spaces.py": b"pass \n",
            "crlf.py": b"pass\r\n",
            "newline.py": b"pass",
            "encoding.py": b"\xff\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in cases.items():
                with self.subTest(name=name):
                    (root / name).write_bytes(content)
                    with patch.object(LINT, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                        self.assertFalse(LINT.check_text([name]))
            (root / "valid.py").write_text("pass\n")
            with patch.object(LINT, "ROOT", root):
                self.assertTrue(LINT.check_text(["valid.py"]))

    def test_json_duplicate_keys_and_dotenv_duplicate_assignments_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.json").write_text('{"key": 1, "key": 2}\n')
            (root / ".env.example").write_text("KEY=1\nKEY=2\n")
            with patch.object(LINT, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(LINT.check_json(["bad.json"]))
                self.assertFalse(LINT.check_dotenv([".env.example"]))

    def test_missing_linter_fails_instead_of_silently_skipping_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "test.sh").write_text("#!/bin/sh\ntrue\n")
            with patch.object(LINT, "ROOT", root), patch.object(LINT, "project_files", return_value=["test.sh"]), \
                    patch.object(LINT.shutil, "which", return_value=None), patch("sys.argv", ["lint.py"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(LINT.main(), 1)

    def test_timed_out_cppcheck_fails_and_later_checks_still_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "test.cpp").write_text("int main() { return 0; }\n")
            (root / "README.md").write_text("# Example\n")
            commands = []

            def run(command, **kwargs):
                commands.append(command[0])
                if command[0] == "cppcheck":
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                return subprocess.CompletedProcess(command, 0)

            output = io.StringIO()
            with patch.object(LINT, "ROOT", root), \
                    patch.object(LINT, "project_files", return_value=["test.cpp", "README.md"]), \
                    patch.object(LINT.shutil, "which", return_value="tool"), \
                    patch.object(LINT.subprocess, "run", side_effect=run), \
                    patch("sys.argv", ["lint.py"]), contextlib.redirect_stdout(output):
                self.assertEqual(LINT.main(), 1)
            self.assertEqual(commands, ["cppcheck", "pymarkdown"])
            self.assertIn("timed out after 120 seconds", output.getvalue())

    def test_interrupt_returns_130_without_a_traceback(self):
        output = io.StringIO()
        with patch.object(LINT, "main", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stderr(output):
            self.assertEqual(LINT.cli(), 130)
        self.assertIn("interrupted", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
