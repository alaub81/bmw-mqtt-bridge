#!/usr/bin/env python3
"""Lint all existing tracked and non-ignored untracked files without modifying them."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR_HEADER = "resources/src/json.hpp"


def project_files():
    """Deduplicate staged/untracked paths and omit staged deletions and ignored data."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True,
    )
    return sorted({
        name.decode("utf-8") for name in result.stdout.split(b"\0")
        if name and (ROOT / name.decode("utf-8")).is_file()
    })


def check_text(files):
    failures = []
    for name in files:
        try:
            text = (ROOT / name).read_bytes().decode("utf-8")
        except UnicodeError:
            failures.append(f"{name}: not valid UTF-8")
            continue
        if "\r" in text:
            failures.append(f"{name}: use LF line endings")
        if text and not text.endswith("\n"):
            failures.append(f"{name}: missing final newline")
        for number, line in enumerate(text.splitlines(), 1):
            if re.match(r"^(<{7}|={7}|>{7})(?: |$)", line):
                failures.append(f"{name}:{number}: unresolved merge marker")
            # Markdown permits hard line breaks; keep upstream vendor formatting.
            if not name.endswith(".md") and name != VENDOR_HEADER and line.rstrip() != line:
                failures.append(f"{name}:{number}: trailing whitespace")
    if failures:
        print("\n".join(failures), flush=True)
    return not failures


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def check_json(files):
    passed = True
    for name in files:
        try:
            json.loads((ROOT / name).read_text(), object_pairs_hook=reject_duplicate_keys)
        except (ValueError, UnicodeError) as error:
            print(f"{name}: {error}", flush=True)
            passed = False
    return passed


def check_dotenv(files):
    passed = True
    for name in files:
        keys = set()
        for number, line in enumerate((ROOT / name).read_text().splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if not match or match[1] in keys:
                print(f"{name}:{number}: expected a unique KEY=value assignment", flush=True)
                passed = False
            else:
                keys.add(match[1])
    return passed


def main():
    if len(sys.argv) != 1:
        print("Usage: ./tests/linter-check.sh (read-only; no arguments)", file=sys.stderr)
        return 2
    try:
        files = project_files()
    except subprocess.CalledProcessError as error:
        print(f"Cannot list project files: {error}", file=sys.stderr)
        return 1
    if not files:
        print("No project files found.", file=sys.stderr)
        return 1
    results = []

    def check(label, paths, command=None, function=None, timeout=None):
        if not paths:
            return
        print(f"\n==> {label} ({len(paths)} files)", flush=True)
        if command:
            if not shutil.which(command[0]):
                print(f"Missing tool: {command[0]}. See README.md (Lint checks).", flush=True)
                passed = False
            else:
                try:
                    passed = subprocess.run(command + paths, cwd=ROOT, timeout=timeout).returncode == 0
                except subprocess.TimeoutExpired:
                    print(f"{label}: timed out after {timeout} seconds; check failed.", flush=True)
                    passed = False
        else:
            passed = function(paths)
        results.append((label, passed))

    check("Text hygiene / file coverage", files, function=check_text)
    check("JSON syntax and duplicate keys",
          [p for p in files if p.endswith(".json")],
          function=check_json)
    check("Dotenv examples", [p for p in files if p.endswith(".env.example") or Path(p).name == ".env.example"],
          function=check_dotenv)
    check("ShellCheck", [p for p in files if p.endswith(".sh")], command=["shellcheck"])
    check("Hadolint", [p for p in files if Path(p).name.startswith("Dockerfile")],
          command=["hadolint", "--config", ".hadolint.yaml"])
    check("YAML", [p for p in files if p.endswith((".yml", ".yaml"))],
          command=["yamllint", "--strict", "--config-file", ".yamllint.yaml"])
    check("GitHub Actions", [p for p in files if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))],
          command=["actionlint", "-pyflakes="])
    check("Python", [p for p in files if p.endswith(".py")],
          command=["ruff", "check", "--config", "ruff.toml"])
    check("C++", [p for p in files if p.endswith((".cpp", ".cc", ".cxx", ".h", ".hpp")) and p != VENDOR_HEADER],
          command=["cppcheck", "--std=c++17", "--check-level=normal", "--report-progress",
                   "--enable=warning,performance,portability",
                   "--error-exitcode=1", "--inline-suppr", "--max-configs=1", "--suppress=missingIncludeSystem",
                   "--suppress=toomanyconfigs", "--suppress=normalCheckLevelMaxBranches",
                   f"--suppress=*:{VENDOR_HEADER}"], timeout=120)
    check("Markdown", [p for p in files if p.endswith(".md")],
          command=["pymarkdown", "--config", ".pymarkdown.yaml", "--strict-config", "scan"])
    print("\n================ Summary ================", flush=True)
    print(f"All {len(files)} project files received the text hygiene check.", flush=True)
    print("Vendor json.hpp receives text checks and is parsed as a C++ include; upstream style is preserved.", flush=True)
    for label, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}  {label}", flush=True)
    return 0 if all(passed for _, passed in results) else 1


def cli():
    try:
        return main()
    except KeyboardInterrupt:
        print("\nLint checks interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(cli())
