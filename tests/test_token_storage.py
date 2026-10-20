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
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'bmw_token_store'
        subprocess.run(['c++', '-std=c++17', '-pthread', '-I', str(ROOT / 'resources/src'),
                        str(ROOT / 'resources/src/bmw_token_store.cpp'), '-o', str(cls.binary)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.mock_bin = self.base / 'bin'
        self.mock_bin.mkdir()
        self.state = self.base / 'state'
        self.flow = self.base / 'bmw_flow.sh'
        shutil.copyfile(ROOT / 'resources/bmw_flow.sh', self.flow)
        shutil.copy(self.binary, self.base / 'bmw_token_store')
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
                   BMB_BMW_CLIENT_ID='test-client', BMB_BMW_GCID='test-account',
                   BMB_BMW_TOKEN_DIR=str(self.state), TEST_TOKEN_RESPONSE=str(response_path))
        env.update(overrides)
        return subprocess.run(
            ['bash', '-c', 'umask 000; exec bash "$1"', '_', str(self.flow)],
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
                path = self.state / 'tokens.json'
                self.assertEqual(json.loads(path.read_text()), dict(self.tokens, version=1))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                if existing:
                    for token in self.tokens:
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

    def test_bundle_commit_failure_preserves_previous_generation(self):
        self.existing_tokens()
        bundle = self.state / 'tokens.json'
        bundle.write_text(json.dumps(dict(self.tokens, version=1)))
        previous = bundle.read_bytes()
        (self.base / 'bmw_token_store').write_text('#!/bin/sh\nexit 1\n')
        result = self.run_authentication()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(bundle.read_bytes(), previous)
        self.assert_existing_tokens_unchanged()

    def test_directory_bundle_target_preserves_legacy_files_and_cleans_staging(self):
        self.existing_tokens()
        target = self.state / 'tokens.json'
        target.mkdir()
        (target / 'sentinel').write_text('keep-me')
        result = self.run_authentication()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / 'sentinel').read_text(), 'keep-me')
        self.assert_existing_tokens_unchanged()
        self.assertFalse(list(self.state.glob('tokens.json.tmp.*')))

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_refresh_writer_restricts_permissions_and_preserves_failed_target(self):
        source = (ROOT / 'resources/src/atomic_file.hpp').read_text()
        helper = source[source.index('static bool write_file_atomic('):]
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
#include <sys/wait.h>
#include <unistd.h>
'''+ r'''
static int fault = 0;
static int checked_mkstemp(char* path) {
    if (fault == 1) { errno = EROFS; return -1; }
    return ::mkstemp(path);
}
static ssize_t checked_write(int fd, const void* data, size_t size) {
    if (fault == 2) { errno = ENOSPC; return -1; }
    if (fault == 3) { fault = 0; errno = EINTR; return -1; }
    return ::write(fd, data, size);
}
static int checked_fsync(int fd) {
    if (fault == 4) { errno = EIO; return -1; }
    return ::fsync(fd);
}
static int checked_rename(const char* from, const char* to) {
    if (fault == 5) { errno = EACCES; return -1; }
    if (fault == 6) _exit(75); // Process dies after flush, before commit.
    return ::rename(from, to);
}
''' + helper.replace('::mkstemp(', 'checked_mkstemp(').replace('::write(', 'checked_write(').replace('::fsync(', 'checked_fsync(').replace('::rename(', 'checked_rename(') + r'''
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
    for (int failure : {1, 2, 4, 5}) {
        fault = failure;
        assert(!write_file_atomic(path, "must-not-replace"));
        { std::ifstream input(path); input >> content; }
        assert(content == "rotated-token");
        for (auto& entry : std::filesystem::directory_iterator(std::filesystem::path(path).parent_path()))
            assert(entry.path().filename().string().find(".tmp.") == std::string::npos);
    }
    fault = 6;
    const pid_t child = fork();
    assert(child >= 0);
    if (child == 0) { write_file_atomic(path, "interrupted-commit"); _exit(76); }
    int status = 0;
    assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 75);
    { std::ifstream input(path); input >> content; }
    assert(content == "rotated-token");
    // A crash may leave staging files, but a later writer must still commit safely.
    fault = 0;
    assert(write_file_atomic(path, "rotated-token"));
    for (auto& entry : std::filesystem::directory_iterator(std::filesystem::path(path).parent_path()))
        if (entry.path().filename().string().find(".tmp.") != std::string::npos)
            std::filesystem::remove(entry.path());
    fault = 3; // Interrupted writes are retried rather than losing the new token.
    assert(write_file_atomic(path, "after-interruption"));
    { std::ifstream input(path); input >> content; }
    assert(content == "after-interruption");
    fault = 0;
    const auto blocked = std::filesystem::path(path).parent_path() / "blocked";
    std::filesystem::create_directory(blocked);
    { std::ofstream output(blocked / "sentinel"); output << "keep-me"; }
    assert(!write_file_atomic(blocked.string(), "replacement"));
    { std::ifstream input(blocked / "sentinel"); input >> content; }
    assert(content == "keep-me");
}
''')
            binary = base / 'writer-test'
            subprocess.run(['c++', '-include', str(ROOT / 'resources/src/bridge_log.hpp'), '-std=c++17', str(test_source), '-o', str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary), str(base / 'token')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(list(base.glob('*.tmp.*')))


if __name__ == '__main__':
    unittest.main()
