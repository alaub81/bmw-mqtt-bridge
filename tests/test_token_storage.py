"""Offline checks for owner-only token storage and atomic replacement."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TokenStorageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('jq') and shutil.which('openssl'), 'OAuth helper tools unavailable')
    def test_authentication_restricts_new_and_existing_token_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            mock_bin = base / 'bin'
            mock_bin.mkdir()
            curl = mock_bin / 'curl'
            curl.write_text(r'''#!/bin/sh
case "$*" in
  *oauth/device/code*)
    printf '%s\n' '{"device_code":"test-device","verification_uri_complete":"https://example.invalid/verify"}'
    ;;
  *oauth/token*)
    printf '%s\n' '{"access_token":"new-access","id_token":"new-id","refresh_token":"new-refresh"}'
    ;;
  *) exit 2 ;;
esac
''')
            curl.chmod(0o755)
            expected = {'access_token.txt': 'new-access', 'id_token.txt': 'new-id',
                        'refresh_token.txt': 'new-refresh'}
            for existing in (False, True):
                with self.subTest(existing=existing):
                    state = base / ('existing' if existing else 'fresh')
                    if existing:
                        state.mkdir()
                        for filename in expected:
                            token = state / filename
                            token.write_text('old-token')
                            token.chmod(0o644)
                    env = dict(os.environ, PATH=f'{mock_bin}:{os.environ["PATH"]}',
                               BMW_CLIENT_ID='test-client', BMW_GCID='test-account',
                               BMW_TOKEN_DIR=str(state))
                    result = subprocess.run(
                        ['bash', '-c', 'umask 000; exec bash "$1"', '_', str(ROOT / 'resources/bmw_flow.sh')],
                        input='\n', env=env, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    for filename, content in expected.items():
                        token = state / filename
                        self.assertEqual(token.read_text().strip(), content)
                        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)

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
