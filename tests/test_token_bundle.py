"""Restart/migration tests using the production token store and atomic writer."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TokenBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        source = cls.base / 'store.cpp'
        # Inject crashes immediately before/after the actual commit syscall.
        atomic = (ROOT / 'resources/src/atomic_file.hpp').read_text()
        atomic = atomic[atomic.index('static bool write_file_atomic('):]
        source.write_text(r'''
#include <cassert>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include "bridge_log.hpp"
#include "token_store.hpp"
static std::string mode;
static int flush(int fd) {
    struct stat info{};
    if (mode == "syncfail" && fstat(fd, &info) == 0 && S_ISDIR(info.st_mode)) {
        errno = EIO; return -1;
    }
    return ::fsync(fd);
}
static int commit(const char* from, const char* to) {
    if (mode == "before") _exit(70);
    if (mode == "fail") { errno = EIO; return -1; }
    const int result = ::rename(from, to);
    if (mode == "after") _exit(71);
    return result;
}
''' + atomic.replace('::rename(', 'commit(').replace('::fsync(', 'flush(') + r'''
int main(int argc, char** argv) {
    mode = argv[1];
    const std::filesystem::path directory = argv[2];
    token_validation::Tokens tokens;
    if (mode == "read") {
        if (!token_store::load(directory, tokens)) return 1;
        std::cout << nlohmann::json{{"id_token", tokens.id}, {"refresh_token", tokens.refresh},
                                  {"access_token", tokens.access}} << '\n';
        return 0;
    }
    if (mode == "migrate") {
        if (!token_store::load(directory, tokens)) return 1;
    } else tokens = {"new-id", "new-refresh", "new-access", 0};
    return token_store::save(directory, tokens, write_file_atomic) ? 0 : 1;
}
''')
        cls.binary = cls.base / 'store'
        subprocess.run(['c++', '-std=c++17', '-pthread', '-I', str(ROOT / 'resources/src'),
                        str(source), '-o', str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.directory = Path(self.state.name)
        self.old = {'id_token': 'old-id', 'refresh_token': 'old-refresh', 'access_token': 'old-access'}

    def run_store(self, mode, expected=0):
        result = subprocess.run([str(self.binary), mode, str(self.directory)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def legacy(self):
        for key, value in self.old.items():
            (self.directory / f'{key}.txt').write_text('  ' + value + '\n')

    def read(self):
        # New process each time: tests actual restart selection, not in-memory state.
        return json.loads(self.run_store('read').stdout)

    def test_migration_preserves_credentials_and_ignores_later_legacy_changes(self):
        self.legacy()
        self.run_store('migrate')
        bundle = self.directory / 'tokens.json'
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o600)
        (self.directory / 'refresh_token.txt').write_text('stale-refresh')
        self.assertEqual(self.read(), self.old)

    def test_migration_supports_missing_access_token(self):
        self.legacy()
        (self.directory / 'access_token.txt').unlink()
        self.run_store('migrate')
        self.assertEqual(self.read(), dict(self.old, access_token=''))

    def test_crash_before_commit_keeps_old_bundle_and_after_commit_loads_entire_new_set(self):
        self.legacy()
        self.run_store('migrate')
        self.run_store('before', 70)
        self.assertEqual(self.read(), self.old)
        self.assertTrue(list(self.directory.glob('tokens.json.tmp.*')))
        self.run_store('after', 71)
        self.assertEqual(self.read(), {'id_token': 'new-id', 'refresh_token': 'new-refresh', 'access_token': 'new-access'})

    def test_interrupted_first_migration_retries_from_legacy_without_partial_state(self):
        self.legacy()
        self.run_store('before', 70)
        self.assertFalse((self.directory / 'tokens.json').exists())
        self.assertEqual(self.read(), self.old)
        self.run_store('migrate')
        self.assertEqual(self.read(), self.old)

    def test_failed_commit_cleans_staging_and_preserves_all_old_tokens(self):
        self.legacy()
        self.run_store('migrate')
        self.run_store('fail', 1)
        self.assertEqual(self.read(), self.old)
        self.assertFalse(list(self.directory.glob('tokens.json.tmp.*')))

    def test_directory_sync_failure_reports_failure_but_keeps_complete_committed_set(self):
        self.legacy()
        self.run_store('migrate')
        self.run_store('syncfail', 1)
        self.assertEqual(self.read(), {'id_token': 'new-id', 'refresh_token': 'new-refresh', 'access_token': 'new-access'})

    def test_corrupt_or_unsupported_bundle_never_falls_back_to_legacy(self):
        self.legacy()
        for value in ['not json', '{}', '[]', json.dumps(dict(self.old, version=2)), json.dumps(dict(self.old, version=1.0)),
                      json.dumps(dict(self.old, version=1, refresh_token='')), 'x' * (1024 * 1024 + 1)]:
            (self.directory / 'tokens.json').write_text(value)
            self.run_store('read', 1)
        (self.directory / 'tokens.json').unlink()
        (self.directory / 'tokens.json').symlink_to(self.directory / 'missing')
        self.run_store('read', 1)
