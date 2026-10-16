"""Resource accounting boundaries, partial reads and bounded restart-aware sampling."""

import ast
import argparse
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("resource_soak", Path(__file__).with_name("resource_soak.py"))
resource_soak = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource_soak)


class ResourceSoakTests(unittest.TestCase):
    def setUp(self):
        host = patch.object(resource_soak, "validate_host")
        host.start()
        self.addCleanup(host.stop)
        command = patch.object(resource_soak, "command", return_value="bmw-mqtt-bridge")
        command.start()
        self.addCleanup(command.stop)

    def test_cpu_delta_uses_one_core_and_rejects_missing_reset_or_bad_time(self):
        self.assertEqual(resource_soak.cpu_percent(100000, 700000, 60), 1)
        for old, new, elapsed in ((None, 1, 60), (1, None, 60), (2, 1, 60), (0, 1, 0)):
            self.assertIsNone(resource_soak.cpu_percent(old, new, elapsed))

    def test_process_memory_marks_partial_and_missing_reads(self):
        def read(path):
            if str(path) == "/proc/1/status":
                return "VmRSS:\t100 kB\nVmHWM:\t120 kB\nThreads:\t2\n"
            raise FileNotFoundError

        with patch.object(Path, "read_text", autospec=True, side_effect=read):
            values = resource_soak.process_memory(["1", "2"])
            self.assertEqual(values["processRssSumBytes"], 102400)
            self.assertEqual(values["processPeakRssSumBytes"], 122880)
            self.assertEqual((values["processesExpected"], values["processesRead"]), (2, 1))
            self.assertIsNone(resource_soak.process_memory(["2"])["processRssSumBytes"])

    def test_cgroup_root_and_missing_controller_do_not_substitute_host_memory(self):
        with patch.object(Path, "read_text", return_value="0::/\n"):
            self.assertEqual(
                resource_soak.cgroup_metrics("1"),
                {"cgroupCpuUsec": None, "cgroupMemoryBytes": None, "cgroupIo": None},
            )

        def read(path):
            if str(path) == "/proc/1/cgroup":
                return "0::/system.slice/fixture.scope\n"
            if path.name == "cpu.stat":
                return "usage_usec 12345\nuser_usec 10000\nsystem_usec 2345\n"
            raise FileNotFoundError

        with patch.object(Path, "read_text", autospec=True, side_effect=read):
            self.assertEqual(
                resource_soak.cgroup_metrics("1"),
                {"cgroupCpuUsec": 12345, "cgroupMemoryBytes": None, "cgroupIo": None},
            )

    def test_bounded_collection_marks_restart_gaps_and_does_not_save_container_identity(self):
        now = 0

        def sleep(seconds):
            nonlocal now
            now += seconds

        observations = [
            ({"cgroupCpuUsec": 100000}, ("private-container", "first-start"), None),
            ({"cgroupCpuUsec": 700000}, ("private-container", "first-start"), None),
            ({"status": "unavailable"}, None, None),
            ({"cgroupCpuUsec": 10000}, ("private-container", "new-start"), None),
            ({"cgroupCpuUsec": 610000}, ("private-container", "new-start"), None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "samples.jsonl"
            with (
                patch(
                    "sys.argv",
                    ["resource_soak", "--duration-seconds", "240", "--output", str(output)],
                ),
                patch.object(resource_soak, "sample", side_effect=observations) as sample,
                patch.object(resource_soak.time, "monotonic", side_effect=lambda: now),
                patch.object(resource_soak.time, "sleep", side_effect=sleep),
            ):
                self.assertEqual(resource_soak.main(), 0)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(sample.call_count, 5)
            self.assertEqual([r["elapsedSeconds"] for r in rows], [0, 60, 120, 180, 240])
            self.assertEqual([r["cpuPercentOneCore"] for r in rows], [None, 1, None, None, 1])
            self.assertEqual([r["generation"] for r in rows], [1, 1, 2, 3, 3])
            self.assertNotIn("private-container", output.read_text())
            with patch("sys.argv", ["resource_soak", "--output", str(output)]):
                with self.assertRaises(FileExistsError):
                    resource_soak.main()

    def test_host_collector_syntax_remains_python_39_compatible(self):
        ast.parse(Path(resource_soak.__file__).read_text(), feature_version=(3, 9))

    def test_cgroup_io_retains_devices_zero_and_ignores_extra_kernel_metrics(self):
        content = (
            "8:0 rbytes=10 wbytes=20 rios=1 wios=2 dbytes=0 dios=0 avg_lat=1.5\n"
            "8:16 rbytes=0 wbytes=0 rios=0 wios=0\n"
        )
        with patch.object(Path, "read_text", return_value=content):
            self.assertEqual(
                resource_soak.cgroup_io(Path("/fixture")),
                {
                    "8:0": {"rbytes": 10, "wbytes": 20, "rios": 1, "wios": 2},
                    "8:16": {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0},
                },
            )
        with patch.object(Path, "read_text", return_value=""):
            self.assertEqual(resource_soak.cgroup_io(Path("/fixture")), {})

    def test_cgroup_io_missing_malformed_negative_or_duplicate_is_unavailable(self):
        for content in (
            "8:0 rbytes=0 wbytes=0 rios=0",
            "8:0 rbytes=-1 wbytes=0 rios=0 wios=0",
            "8:0 rbytes=bad wbytes=0 rios=0 wios=0",
            "8:0 rbytes=0 rbytes=1 wbytes=0 rios=0 wios=0",
            "device rbytes=0 wbytes=0 rios=0 wios=0",
            "8:0 rbytes=0 wbytes=0 rios=0 wios=0\n8:0 rbytes=1 wbytes=0 rios=0 wios=0",
            "\n",
        ):
            with (
                self.subTest(content=content),
                patch.object(Path, "read_text", return_value=content),
            ):
                self.assertIsNone(resource_soak.cgroup_io(Path("/fixture")))
        with patch.object(Path, "read_text", side_effect=PermissionError):
            self.assertIsNone(resource_soak.cgroup_io(Path("/fixture")))

    def test_missing_cpu_controller_does_not_hide_available_io_or_memory(self):
        def read(path):
            if path.name == "cgroup":
                return "0::/fixture.scope\n"
            if path.name == "memory.current":
                return "1024"
            if path.name == "io.stat":
                return "8:0 rbytes=0 wbytes=100 rios=0 wios=1\n"
            raise FileNotFoundError

        with patch.object(Path, "read_text", autospec=True, side_effect=read):
            self.assertEqual(
                resource_soak.cgroup_metrics("1"),
                {
                    "cgroupCpuUsec": None,
                    "cgroupMemoryBytes": 1024,
                    "cgroupIo": {"8:0": {"rbytes": 0, "wbytes": 100, "rios": 0, "wios": 1}},
                },
            )

    def test_process_io_marks_partial_reads_and_keeps_start_time_internal(self):
        def read(path):
            if str(path) == "/proc/1/stat":
                return "1 (fixture process) name) S " + "0 " * 18 + "123 0\n"
            if str(path) == "/proc/1/io":
                return "\n".join(key + ": 10" for key in resource_soak.PROCESS_IO_FIELDS)
            raise FileNotFoundError

        with patch.object(Path, "read_text", autospec=True, side_effect=read):
            row, counters = resource_soak.process_io(["1"])
            self.assertEqual(counters[("1", 123)]["wchar"], 10)
            self.assertEqual(row["processIo"], dict.fromkeys(resource_soak.PROCESS_IO_FIELDS, 10))
            self.assertEqual((row["processIoExpected"], row["processIoRead"]), (1, 1))
            row, counters = resource_soak.process_io(["1", "2"])
            self.assertEqual((row["processIoExpected"], row["processIoRead"]), (2, 1))
            self.assertIsNone(counters)
            self.assertIsNone(resource_soak.process_io(["2"])[0]["processIo"])
        for content in ("", "1 (broken)", "1 (fixture) S " + "0 " * 18 + "bad"):
            with (
                self.subTest(content=content),
                patch.object(Path, "read_text", return_value=content),
            ):
                self.assertEqual(resource_soak.process_io(["1"])[0]["processIoRead"], 0)

    def test_counter_rates_reject_resets_membership_pid_reuse_and_missing_reads(self):
        old = {("1", 123): {"wchar": 100, "syscw": 2}}
        new = {("1", 123): {"wchar": 220, "syscw": 8}}
        self.assertEqual(
            resource_soak.counter_rates(old, new, 60),
            {
                ("1", 123): {"wchar": 2, "syscw": 0.1},
            },
        )
        for previous, current, elapsed in (
            (None, new, 60),
            (old, None, 60),
            (old, new, 0),
            (old, {("1", 124): {"wchar": 220, "syscw": 8}}, 60),
            (old, {("1", 123): {"wchar": 220, "syscw": 1}}, 60),
            (old, {("1", 123): {"wchar": 220}}, 60),
            (old, {}, 60),
        ):
            with self.subTest(current=current):
                self.assertIsNone(resource_soak.counter_rates(previous, current, elapsed))
        self.assertEqual(resource_soak.counter_rates({}, {}, 60), {})

    def test_recorded_io_rates_reset_across_restarts_and_never_save_process_identity(self):
        now = 0

        def sleep(seconds):
            nonlocal now
            now += seconds

        def observation(value, start="first", partial=False):
            values = {key: value for key in resource_soak.PROCESS_IO_FIELDS}
            io = {"8:0": {key: value for key in resource_soak.CGROUP_IO_FIELDS}}
            return (
                {"cgroupIo": io, "processIo": values},
                ("private-container", start),
                (None if partial else {("private-pid", 123): values}),
            )

        observations = [
            observation(10),
            observation(70),
            observation(130, partial=True),
            observation(190),
            observation(5, start="second"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "io.jsonl"
            with (
                patch(
                    "sys.argv",
                    ["resource_soak", "--duration-seconds", "240", "--output", str(output)],
                ),
                patch.object(resource_soak, "sample", side_effect=observations),
                patch.object(resource_soak.time, "monotonic", side_effect=lambda: now),
                patch.object(resource_soak.time, "sleep", side_effect=sleep),
            ):
                self.assertEqual(resource_soak.main(), 0)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(rows[1]["processIoPerSecond"]["syscw"], 1)
            self.assertEqual(rows[1]["cgroupIoPerSecond"]["8:0"]["wbytes"], 1)
            self.assertIsNone(rows[2]["processIoPerSecond"])
            self.assertIsNone(rows[3]["processIoPerSecond"])
            self.assertIsNone(rows[4]["processIoPerSecond"])
            self.assertIsNone(rows[4]["cgroupIoPerSecond"])
            self.assertNotIn("private", output.read_text())

    def test_custom_service_and_compose_file_are_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "samples.jsonl"
            with (
                patch("sys.argv", ["resource_soak", "--compose-file", "custom.yml",
                      "--service", "bridge", "--duration-seconds", "1",
                      "--output", str(output)]),
                patch.object(resource_soak, "command", return_value="bridge") as command,
                patch.object(resource_soak, "sample", return_value=({}, None, None)) as sample,
                patch.object(resource_soak.time, "monotonic", side_effect=[0, 1]),
            ):
                self.assertEqual(resource_soak.main(), 0)
                command.assert_called_once_with(
                    ["docker", "compose", "-f", "custom.yml", "config", "--services"]
                )
                sample.assert_called_once_with(["docker", "compose", "-f", "custom.yml"], "bridge")

    def test_missing_service_and_invalid_limits_fail_before_output_creation(self):
        for arguments in (["--service", "missing"], ["--duration-seconds", "86401"],
                          ["--interval-seconds", "0"], ["--service", "bad service"]):
            with (
                self.subTest(arguments=arguments),
                tempfile.TemporaryDirectory() as directory,
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                output = Path(directory) / "samples.jsonl"
                with patch("sys.argv", ["resource_soak", "--output", str(output)] + arguments):
                    with self.assertRaises(SystemExit) as error:
                        resource_soak.main()
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())

    def test_sampling_targets_only_requested_service_and_handles_bad_inspect(self):
        with patch.object(resource_soak, "command", side_effect=["private-id", "1 bad running none date"]) as command:
            row, identity, counters = resource_soak.sample(["docker", "compose"], "bridge")
            self.assertEqual(command.call_args_list[0].args[0],
                             ["docker", "compose", "ps", "--all", "-q", "bridge"])
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(identity)
            self.assertIsNone(counters)
        with patch.object(resource_soak, "command", return_value="first\nsecond") as command:
            self.assertIsNone(resource_soak.sample(["docker", "compose"])[1])
            self.assertEqual(command.call_count, 1)


class HostValidationTests(unittest.TestCase):
    def test_local_host_and_context_override(self):
        for environment, responses, calls in (
            ({"DOCKER_HOST": "unix:///var/run/docker.sock"}, ["linux"], 1),
            ({}, ["unix:///var/run/docker.sock", "linux"], 2),
            ({"DOCKER_HOST": "ssh://remote", "DOCKER_CONTEXT": "local"},
             ["unix:///var/run/docker.sock", "linux"], 2),
        ):
            with (
                self.subTest(environment=environment),
                patch.object(resource_soak.sys, "platform", "linux"),
                patch.dict(resource_soak.os.environ, environment, clear=True),
                patch.object(resource_soak, "command", side_effect=responses) as command,
            ):
                resource_soak.validate_host(argparse.ArgumentParser())
                self.assertEqual(command.call_count, calls)

    def test_non_linux_remote_and_unavailable_daemon_are_rejected(self):
        for platform, endpoint, response in (
            ("darwin", "unix:///var/run/docker.sock", "linux"),
            ("linux", "ssh://remote", "linux"),
            ("linux", "tcp://remote:2376", "linux"),
            ("linux", "unix:///var/run/docker.sock", None),
        ):
            with (
                self.subTest(platform=platform, endpoint=endpoint),
                patch.object(resource_soak.sys, "platform", platform),
                patch.dict(resource_soak.os.environ, {"DOCKER_HOST": endpoint}, clear=True),
                patch.object(resource_soak, "command", return_value=response),
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                with self.assertRaises(SystemExit) as error:
                    resource_soak.validate_host(argparse.ArgumentParser())
                self.assertEqual(error.exception.code, 2)
