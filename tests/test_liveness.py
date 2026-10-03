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
        result = subprocess.run(['docker', 'compose', '--env-file', '.env.sample',
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
                       HEALTH_MQTT_DISCONNECT_TIMEOUT='120',
                       TEST_PROC_EXE=str(process_exe), TEST_BINARY=str(binary))
            def check():
                return subprocess.run(['sh', '-c', command], env=env,
                                      capture_output=True).returncode
            cases = [
                ('fresh', f'{int(time.time())} 1 0 0\n', 0),
                ('stale', f'{int(time.time()) - 120} 1 0 0\n', 1),
                ('future', f'{int(time.time()) + 120} 1 0 0\n', 1),
                ('wrong-pid', f'{int(time.time())} 42 0 0\n', 1),
                ('short-outage', f'{int(time.time())} 1 119 119\n', 0),
                ('local-outage', f'{int(time.time())} 1 120 0\n', 1),
                ('bmw-outage', f'{int(time.time())} 1 0 120\n', 1),
                ('both-outage', f'{int(time.time())} 1 180 180\n', 1),
                ('recovered', f'{int(time.time())} 1 0 0\n', 0),
                ('negative-downtime', f'{int(time.time())} 1 -1 0\n', 1),
                ('old-format', f'{int(time.time())} 1\n', 1),
                ('bad-downtime', f'{int(time.time())} 1 invalid 0\n', 1),
                ('empty', '', 1),
                ('malformed', 'bad timestamp\n', 1),
            ]
            for label, content, expected in cases:
                with self.subTest(case=label):
                    heartbeat.write_text(content)
                    if expected == 0:
                        self.assertEqual(check(), 0)
                    else:
                        self.assertNotEqual(check(), 0)
            # Exercise the application's default path and an explicit override.
            heartbeat.write_text(f'{int(time.time())} 1 0 0\n')
            default_command = command.replace('/tmp/bmw-mqtt-bridge-heartbeat', str(heartbeat))
            default_env = dict(env)
            default_env.pop('BMW_HEARTBEAT_FILE', None)
            result = subprocess.run(['sh', '-c', default_command], env=default_env,
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            default_env['BMW_HEARTBEAT_FILE'] = str(base / 'missing-override')
            result = subprocess.run(['sh', '-c', default_command], env=default_env,
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            heartbeat.unlink()
            self.assertNotEqual(check(), 0)
            heartbeat.write_text(f'{int(time.time())} 1 120 0\n')
            env['HEALTH_MQTT_DISCONNECT_TIMEOUT'] = '180'
            self.assertEqual(check(), 0)
            env['HEALTH_MQTT_DISCONNECT_TIMEOUT'] = '0'
            self.assertNotEqual(check(), 0)
            env['HEALTH_MQTT_DISCONNECT_TIMEOUT'] = '120'
            heartbeat.write_text(f'{int(time.time())} 1 0 0\n')
            process_exe.unlink()
            process_exe.touch()  # A different executable must fail even if fresh.
            self.assertNotEqual(check(), 0)

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_production_heartbeat_writer(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helper = source.split('// Health telemetry: main-loop liveness and continuous MQTT downtime.')[1]
        helper = helper.split('// helper: simple placeholder check')[0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / 'writer.cpp'
            path.write_text(r'''
#include <cassert>
#include <chrono>
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
    long timestamp = 0, pid = 0, local_offline = -1, bmw_offline = -1;
    { std::ifstream input(path); input >> timestamp >> pid >> local_offline >> bmw_offline; }
    assert(timestamp >= time(nullptr) - 2 && timestamp <= time(nullptr));
    assert(pid == getpid());
    assert(local_offline == 0 && bmw_offline == 0);
    assert(write_heartbeat(path, 125, 0)); // Existing heartbeat is replaced atomically.
    { std::ifstream input(path); input >> timestamp >> pid >> local_offline >> bmw_offline; }
    assert(local_offline == 125 && bmw_offline == 0);
    const auto start = std::chrono::steady_clock::time_point{};
    auto local_last_online = start, bmw_last_online = start;
    assert(mqtt_offline_seconds(false, start + std::chrono::seconds(120), local_last_online) == 120);
    // A watchdog rebuild does not reset downtime while the connection remains down.
    assert(mqtt_offline_seconds(false, start + std::chrono::seconds(150), local_last_online) == 150);
    assert(mqtt_offline_seconds(true, start + std::chrono::seconds(151), local_last_online) == 0);
    assert(mqtt_offline_seconds(false, start + std::chrono::seconds(160), local_last_online) == 9);
    assert(mqtt_offline_seconds(false, start + std::chrono::seconds(160), bmw_last_online) == 160);
    assert(mqtt_offline_seconds(true, start + std::chrono::seconds(161), bmw_last_online) == 0);
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
