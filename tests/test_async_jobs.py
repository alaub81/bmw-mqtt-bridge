"""Exercise non-blocking result polling, single-flight work and safe completion."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AsyncJobTests(unittest.TestCase):
    def test_blocked_work_polling_overlap_results_exceptions_and_join(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'jobs.cpp'
            path.write_text(r'''
#include <cassert>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include "async_job.hpp"
int main() {
    std::mutex mutex;
    std::condition_variable condition;
    bool entered = false, release = false;
    AsyncJob<int> job;
    assert(!job.running() && !job.poll() && !job.finish());
    assert(job.start([&] {
        std::unique_lock<std::mutex> lock(mutex);
        entered = true;
        condition.notify_all();
        condition.wait(lock, [&] { return release; });
        return 42;
    }));
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(condition.wait_for(lock, std::chrono::seconds(5), [&] { return entered; }));
    }
    bool overlapping_operation_ran = false;
    assert(!job.start([&] { overlapping_operation_ran = true; return 99; }));
    int main_loop_ticks = 0;
    for (int i = 0; i < 100; ++i) {
        assert(!job.poll()); // Would deadlock if polling waited for the blocked operation.
        ++main_loop_ticks;
    }
    assert(main_loop_ticks == 100 && job.running() && !overlapping_operation_ran);
    {
        std::lock_guard<std::mutex> lock(mutex);
        release = true;
    }
    condition.notify_all();
    assert(job.finish() == 42 && !job.running() && !job.poll());
    assert(job.start([]() -> int { throw std::runtime_error("sensitive-token-body"); }));
    assert(job.finish() == 0 && !job.running());
    assert(job.start([] { return 7; }));
    assert(job.finish() == 7);
}
''')
            binary = Path(directory) / 'jobs'
            result = subprocess.run(['c++', '-std=c++17', '-pthread', '-Wall', '-Wextra',
                                     '-I', str(ROOT / 'resources/src'), str(path), '-o', str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('background operation failed', result.stderr)
            self.assertNotIn('sensitive-token-body', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
