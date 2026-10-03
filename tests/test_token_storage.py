"""Offline checks for validated, owner-only token storage and atomic replacement."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TokenStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.mock_bin = self.base / 'bin'
        self.mock_bin.mkdir()
        self.state = self.base / 'state'
        self.tokens = {'access_token': 'new-access', 'id_token': 'new-id',
                       'refresh_token': 'new-refresh'}
        self.mock_command('curl', r'''#!/bin/sh
case "$*" in
  *oauth/device/code*)
    printf '%s\n' '{"device_code":"test-device","verification_uri_complete":"https://example.invalid/verify"}'
    ;;
  *oauth/token*) cat "$TEST_TOKEN_RESPONSE" ;;
  *) exit 2 ;;
esac
''')

    def mock_command(self, name, script):
        path = self.mock_bin / name
        path.write_text(script)
        path.chmod(0o755)

    def existing_tokens(self):
        self.state.mkdir()
        for token in self.tokens:
            path = self.state / f'{token}.txt'
            path.write_text(f'old-{token}')
            path.chmod(0o644)

    def run_authentication(self, response=None, **overrides):
        response_path = self.base / 'response.json'
        response_path.write_text(json.dumps(self.tokens) if response is None else response)
        env = dict(os.environ, PATH=f'{self.mock_bin}:{os.environ["PATH"]}',
                   BMW_CLIENT_ID='test-client', BMW_GCID='test-account',
                   BMW_TOKEN_DIR=str(self.state), TEST_TOKEN_RESPONSE=str(response_path))
        env.update(overrides)
        return subprocess.run(
            ['bash', '-c', 'umask 000; exec bash "$1"', '_', str(ROOT / 'resources/bmw_flow.sh')],
            input='\n', env=env, capture_output=True, text=True, timeout=10,
        )

    def assert_existing_tokens_unchanged(self):
        for token in self.tokens:
            self.assertEqual((self.state / f'{token}.txt').read_text(), f'old-{token}')
        self.assertFalse(list(self.state.glob('.bmw-tokens.*')))

    @unittest.skipUnless(shutil.which('jq') and shutil.which('openssl'), 'OAuth helper tools unavailable')
    def test_authentication_restricts_new_and_existing_token_files(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.state = self.base / ('existing' if existing else 'fresh')
                if existing:
                    self.existing_tokens()
                    # An open reader must retain the old data after atomic replacement.
                    readers = {token: (self.state / f'{token}.txt').open() for token in self.tokens}
                    for reader in readers.values():
                        self.addCleanup(reader.close)
                result = self.run_authentication()
                self.assertEqual(result.returncode, 0, result.stderr)
                for token, content in self.tokens.items():
                    path = self.state / f'{token}.txt'
                    self.assertEqual(path.read_text().strip(), content)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    if existing:
                        self.assertEqual(readers[token].read(), f'old-{token}')
                self.assertFalse(list(self.state.glob('.bmw-tokens.*')))

    @unittest.skipUnless(shutil.which('jq') and shutil.which('openssl'), 'OAuth helper tools unavailable')
    def test_invalid_responses_leave_existing_tokens_unchanged(self):
        self.existing_tokens()
        responses = ['{}', '[]', 'null', '42', '"text"', 'not JSON',
                     json.dumps(self.tokens) + '\n' + json.dumps(self.tokens)]
        for token in self.tokens:
            missing = dict(self.tokens)
            missing.pop(token)
            responses.append(json.dumps(missing))
            for invalid in (None, '', ' \t\n', 'token with spaces', 'token\n', '\ntoken', 42, False, [], {}):
                responses.append(json.dumps(dict(self.tokens, **{token: invalid})))
        for response in responses:
            with self.subTest(response=response):
                result = self.run_authentication(response)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Tokens received and saved', result.stdout)
                self.assert_existing_tokens_unchanged()

    @unittest.skipUnless(shutil.which('jq') and shutil.which('openssl'), 'OAuth helper tools unavailable')
    def test_staging_failure_leaves_all_existing_tokens_unchanged(self):
        self.existing_tokens()
        self.mock_command('jq', r'''#!/bin/sh
if [ "$1" = '-r' ] && [ "$2" = '--arg' ] && [ "$4" = 'id_token' ]; then
  printf '%s' 'partial-token'
  exit 1
fi
exec "$TEST_REAL_JQ" "$@"
''')
        result = self.run_authentication(TEST_REAL_JQ=shutil.which('jq'))
        self.assertNotEqual(result.returncode, 0)
        self.assert_existing_tokens_unchanged()

    @unittest.skipUnless(shutil.which('jq') and shutil.which('openssl'), 'OAuth helper tools unavailable')
    def test_failed_rename_preserves_original_files_and_cleans_up(self):
        self.existing_tokens()
        self.mock_command('mv', '#!/bin/sh\nexit 1\n')
        result = self.run_authentication()
        self.assertNotEqual(result.returncode, 0)
        self.assert_existing_tokens_unchanged()

    @unittest.skipUnless(shutil.which('jq') and shutil.which('openssl'), 'OAuth helper tools unavailable')
    def test_directory_target_is_rejected_before_replacing_other_tokens(self):
        self.existing_tokens()
        target = self.state / 'id_token.txt'
        target.unlink()
        target.mkdir()
        (target / 'sentinel').write_text('keep-me')
        result = self.run_authentication()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Token path is a directory', result.stderr)
        self.assertEqual((target / 'sentinel').read_text(), 'keep-me')
        for token in ('access_token', 'refresh_token'):
            self.assertEqual((self.state / f'{token}.txt').read_text(), f'old-{token}')
        self.assertFalse(list(self.state.glob('.bmw-tokens.*')))

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_refresh_writer_restricts_permissions_and_preserves_failed_target(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helper = source[source.index('static bool write_file_atomic('):]
        helper = helper.split('static bool refresh_tokens() {')[0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            test_source = base / 'writer.cpp'
            test_source.write_text(r'''
#include <cassert>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
''' + helper + r'''
int main(int argc, char** argv) {
    umask(0000); // The writer must protect tokens independently of the caller.
    const std::string path = argv[1];
    assert(write_file_atomic(path, "new-token"));
    struct stat info{};
    assert(stat(path.c_str(), &info) == 0);
    assert((info.st_mode & 0777) == 0600);
    assert(chmod(path.c_str(), 0644) == 0);
    assert(write_file_atomic(path, "rotated-token"));
    assert(stat(path.c_str(), &info) == 0);
    assert((info.st_mode & 0777) == 0600);
    std::string content;
    { std::ifstream input(path); input >> content; }
    assert(content == "rotated-token");
    const auto blocked = std::filesystem::path(path).parent_path() / "blocked";
    std::filesystem::create_directory(blocked);
    { std::ofstream output(blocked / "sentinel"); output << "keep-me"; }
    assert(!write_file_atomic(blocked.string(), "replacement"));
    { std::ifstream input(blocked / "sentinel"); input >> content; }
    assert(content == "keep-me");
}
''')
            binary = base / 'writer-test'
            subprocess.run(['c++', '-std=c++17', str(test_source), '-o', str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary), str(base / 'token')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(list(base.glob('*.tmp.*')))


if __name__ == '__main__':
    unittest.main()
