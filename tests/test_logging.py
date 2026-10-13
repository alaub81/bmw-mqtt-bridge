"""Verify lazy log formatting, severity filters and bounded repeat logging."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LoggingTests(unittest.TestCase):
    def test_filters_rate_limits_and_concurrent_complete_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'logging.cpp'
            source.write_text(r'''
#include <cassert>
#include <thread>
#include <vector>
#include "bridge_log.hpp"
int main() {
    int formatted = 0;
    bridge_log::configure("INFO");
    BMB_LOG(debug) << "hidden-telemetry=" << ++formatted << '\n';
    assert(formatted == 0);
    BMB_LOG(info) << "visible-info\n";
    bridge_log::configure("warning");
    BMB_LOG(info) << "hidden-info\n";
    BMB_LOG(warning) << "visible-warning\n";
    bridge_log::configure("error");
    BMB_LOG(warning) << "hidden-warning\n";
    BMB_LOG(error) << "visible-error\n";
    bridge_log::configure("info");
    bridge_log::Limiter limiter;
    const auto now = std::chrono::steady_clock::now();
    assert(limiter.check(bridge_log::Level::warning, now).emit);
    for (int i = 1; i < 30; ++i)
        assert(!limiter.check(bridge_log::Level::warning, now + std::chrono::seconds(i)).emit);
    const auto report = limiter.check(bridge_log::Level::warning, now + std::chrono::seconds(30));
    assert(report.emit && report.suppressed == 29);
    bridge_log::Line(report.suppressed).stream() << "visible-summary\n";
    const auto repeat = [] { BMB_LOG_THROTTLED(warning) << "repeat-site\n"; };
    for (int i = 0; i < 10; ++i) repeat();
    bridge_log::configure("debug");
    repeat(); repeat();
    assert(limiter.check(bridge_log::Level::warning, now).emit);
    BMB_LOG(debug) << "visible-debug=" << ++formatted << '\n';
    assert(formatted == 1);
    bool rejected = false;
    try { bridge_log::configure("verbose"); } catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
    std::vector<std::thread> writers;
    for (int i = 0; i < 4; ++i) writers.emplace_back([i] {
        for (int j = 0; j < 50; ++j) BMB_LOG(info) << "worker=" << i << " index=" << j << '\n';
    });
    for (auto& writer : writers) writer.join();
}
''')
            binary = Path(directory) / 'logging'
            result = subprocess.run(['c++', '-std=c++17', '-pthread', '-Wall', '-Wextra',
                                     '-I', str(ROOT / 'resources/src'), str(source), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('hidden-', result.stderr)
            self.assertIn('suppressed 29 similar messages', result.stderr)
            self.assertIn('visible-debug=1', result.stderr)
            self.assertEqual(result.stderr.count('repeat-site'), 3)
            lines = [line for line in result.stderr.splitlines() if line.startswith('worker=')]
            self.assertEqual(len(lines), 200)
            for line in lines:
                self.assertRegex(line, r'^worker=[0-3] index=\d+$')


if __name__ == '__main__':
    unittest.main()
