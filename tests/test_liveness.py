"""Offline regression checks for the Compose liveness check."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LivenessTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('docker'), 'Compose unavailable')
    def test_compose_heartbeat_and_process_check(self):
        env = dict(os.environ, BMW_CLIENT_ID='test-client', BMW_GCID='test-BMW_GCID')
        result = subprocess.run(['docker', 'compose', '--env-file', '.env.example',
                                 'config', '--format', 'json'], cwd=ROOT, env=env,
                                text=True, capture_output=True, check=True)
        health = json.loads(result.stdout)['services']['bmw-mqtt-bridge']['healthcheck']
        self.assertEqual(health['test'][0], 'CMD-SHELL')
        # Compose config preserves escaped dollars; container commands use one dollar.
        command = health['test'][1].replace('$$', '$')
        probe = 'test /proc/1/exe -ef /app/bmw_mqtt_bridge'
        self.assertIn(probe, command)
        # Supply fake process paths on macOS; run all heartbeat logic unchanged.
        command = command.replace(probe, 'test "$TEST_PROC_EXE" -ef "$TEST_BINARY"')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base / 'bridge'
            binary.touch()
            process_exe = base / 'proc-exe'
            process_exe.symlink_to(binary)
            heartbeat = base / 'heartbeat'
            env.update(BMW_HEARTBEAT_FILE=str(heartbeat),
                       TEST_PROC_EXE=str(process_exe), TEST_BINARY=str(binary))
            def check():
                return subprocess.run(['sh', '-c', command], env=env,
                                      capture_output=True).returncode
            cases = [
                ('fresh', f'{int(time.time())} 1\n', 0),
                ('stale', f'{int(time.time()) - 120} 1\n', 1),
                ('future', f'{int(time.time()) + 120} 1\n', 1),
                ('wrong-pid', f'{int(time.time())} 42\n', 1),
                ('empty', '', 1),
                ('malformed', 'bad timestamp\n', 1),
            ]
            for label, content, expected in cases:
                with self.subTest(case=label):
                    heartbeat.write_text(content)
                    self.assertEqual(check(), expected)
            heartbeat.unlink()
            self.assertNotEqual(check(), 0)
            heartbeat.write_text(f'{int(time.time())} 1\n')
            process_exe.unlink()
            process_exe.touch()  # A different executable must fail even if fresh.
            self.assertNotEqual(check(), 0)

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_production_heartbeat_writer(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helper = source.split('// Local liveness heartbeat; independent of MQTT connection state.')[1]
        helper = helper.split('// helper: simple placeholder check')[0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / 'writer.cpp'
            path.write_text(r'''
#include <cassert>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <string>
#include <unistd.h>
''' + helper + r'''
int main(int argc, char** argv) {
    assert(write_heartbeat(""));
    const std::string path = argv[1];
    assert(write_heartbeat(path));
    long timestamp = 0, pid = 0;
    { std::ifstream input(path); input >> timestamp >> pid; }
    assert(timestamp >= time(nullptr) - 2 && timestamp <= time(nullptr));
    assert(pid == getpid());
    assert(write_heartbeat(path)); // Existing heartbeat is replaced atomically.
    assert(!write_heartbeat(path + "/missing-parent/file"));
}
''')
            binary = base / 'writer-test'
            subprocess.run(['c++', '-std=c++17', str(path), '-o', str(binary)],
                           check=True, capture_output=True)
            heartbeat = base / 'heartbeat'
            subprocess.run([str(binary), str(heartbeat)], check=True)
            self.assertTrue(heartbeat.is_file())
            self.assertFalse(Path(str(heartbeat) + '.tmp').exists())


if __name__ == '__main__':
    unittest.main()
