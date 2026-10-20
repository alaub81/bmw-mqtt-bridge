"""Isolated real-broker tests; run via tests/Dockerfile.integration."""
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import time
import unittest


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'mosquitto.conf'
        self.config.write_text('listener 18883 127.0.0.1\nallow_anonymous true\npersistence false\n')
        self.broker = None
        self.start_broker()
        self.addCleanup(self.stop_broker)
        self.log = open(Path(self.temp.name) / 'harness.log', 'w+')
        self.addCleanup(self.log.close)
        Path('/tmp/cache.json').unlink(missing_ok=True)
        self.publisher = subprocess.Popen(['/test/harness'], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=self.log, text=True)
        self.addCleanup(self.cleanup_publisher)
        self.base = 'homie/bmw-wba00000000000001/'

    def cleanup_publisher(self):
        if self.publisher.poll() is None:
            self.publisher.kill()
        self.publisher.wait(timeout=5)
        self.publisher.stdin.close()
        self.publisher.stdout.close()

    def start_broker(self):
        self.broker = subprocess.Popen(['mosquitto', '-c', str(self.config)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', 18883), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.fail('Broker did not start')

    def stop_broker(self):
        if self.broker and self.broker.poll() is None:
            self.broker.terminate()
            self.broker.wait(timeout=5)

    def command(self, value):
        self.publisher.stdin.write(str(value) + '\n')
        self.publisher.stdin.flush()
        self.assertTrue(select.select([self.publisher.stdout], [], [], 5)[0], 'Publisher timed out')
        self.assertEqual(self.publisher.stdout.readline().strip(), 'done')

    def retained(self, suffix):
        result = subprocess.run(['mosquitto_sub', '-h', '127.0.0.1', '-p', '18883',
                                 '-t', self.base + suffix, '-C', '1', '-W', '5'],
                                capture_output=True, text=True, timeout=7)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_discovery_delta_retention_reconnect_replay_and_shutdown_ack(self):
        self.command(1)
        self.assertEqual(self.retained('$homie'), '4.0.0')
        self.assertEqual(self.retained('$state'), 'ready')
        self.assertEqual(self.retained('telemetry/testValue'), '1')
        observer = subprocess.Popen(['mosquitto_sub', '-h', '127.0.0.1', '-p', '18883',
                                     '-t', self.base + '#', '-R', '-W', '4', '-F', '%t %p'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.2)
            self.command(1)
            self.command(2)
            output, _ = observer.communicate(timeout=6)
        finally:
            if observer.poll() is None:
                observer.kill()
                observer.communicate()
        lines = output.splitlines()
        self.assertEqual([line for line in lines if '/telemetry/testValue ' in line],
                         [self.base + 'telemetry/testValue 2'])
        self.assertFalse(any('/$homie ' in line for line in lines))
        self.stop_broker()
        self.start_broker()  # No persistence: every retained topic must be replayed.
        self.command('tick')
        self.command('tick')
        self.assertEqual(self.retained('$homie'), '4.0.0')
        self.assertEqual(self.retained('telemetry/testValue'), '2')
        self.publisher.stdin.write('stop\n')
        self.publisher.stdin.flush()
        self.assertEqual(self.publisher.wait(timeout=5), 0)
        self.log.seek(0)
        self.assertEqual(self.retained('$state'), 'disconnected', self.log.read())
        self.log.seek(0)
        self.assertNotIn('ack timeout', self.log.read().lower())

    def test_crash_delivers_retained_last_will(self):
        self.command(7)
        self.assertEqual(self.retained('$state'), 'ready')
        self.publisher.kill()
        self.publisher.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.retained('$state') == 'lost':
                return
            time.sleep(0.05)
        self.fail('Last Will was not delivered')


if __name__ == '__main__':
    unittest.main(verbosity=2)
