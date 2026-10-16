#!/usr/bin/env python3
"""Bounded read-only Linux host sampling; requires Docker CLI and Python 3.9+."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROCESS_IO_FIELDS = (
    "rchar",
    "wchar",
    "syscr",
    "syscw",
    "read_bytes",
    "write_bytes",
    "cancelled_write_bytes",
)
CGROUP_IO_FIELDS = ("rbytes", "wbytes", "rios", "wios")


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def integer_file(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def process_memory(pids):
    rss, peak, threads, available = 0, 0, 0, 0
    for pid in pids:
        try:
            fields = {}
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                key, _, value = line.partition(":")
                if key in ("VmRSS", "VmHWM", "Threads"):
                    fields[key] = int(value.split()[0])
            if "VmRSS" not in fields:
                continue
            rss += fields["VmRSS"] * 1024
            peak += fields.get("VmHWM", 0) * 1024
            threads += fields.get("Threads", 0)
            available += 1
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    return {
        "processesExpected": len(pids),
        "processesRead": available,
        "processRssSumBytes": rss if available else None,
        "processPeakRssSumBytes": peak if available else None,
        "processThreads": threads if available else None,
    }


def process_io(pids):
    counters = {}
    for pid in pids:
        try:
            # PID plus kernel start time distinguishes a reused PID. Neither is saved.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            started = int(fields[19])
            values = {}
            for line in Path(f"/proc/{pid}/io").read_text().splitlines():
                key, separator, value = line.partition(":")
                if key in PROCESS_IO_FIELDS:
                    if not separator or key in values:
                        raise ValueError
                    values[key] = int(value)
            if set(values) != set(PROCESS_IO_FIELDS) or min(values.values()) < 0 or started < 0:
                raise ValueError
            counters[(pid, started)] = values
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    return {
        "processIoExpected": len(pids),
        "processIoRead": len(counters),
        "processIo": {
            key: sum(values[key] for values in counters.values()) for key in PROCESS_IO_FIELDS
        }
        if counters
        else None,
    }, counters if counters and len(counters) == len(pids) else None


def cgroup_io(path):
    try:
        devices = {}
        for line in (path / "io.stat").read_text().splitlines():
            device, *fields = line.split()
            if (
                len(device.split(":")) != 2
                or not all(part.isdecimal() for part in device.split(":"))
                or device in devices
            ):
                raise ValueError
            values = {}
            for field in fields:
                key, separator, value = field.partition("=")
                if key in CGROUP_IO_FIELDS:
                    if not separator or key in values:
                        raise ValueError
                    values[key] = int(value)
            if set(values) != set(CGROUP_IO_FIELDS) or min(values.values()) < 0:
                raise ValueError
            devices[device] = values
        return devices
    except (OSError, ValueError):
        return None


def cgroup_metrics(pid):
    result = {"cgroupCpuUsec": None, "cgroupMemoryBytes": None, "cgroupIo": None}
    try:
        group = next(
            line[3:]
            for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
        path = Path("/sys/fs/cgroup") / group.lstrip("/")
        # Never fall back to root-cgroup totals if the container group is unknown.
        if group == "/" or ".." in Path(group).parts:
            return result
    except (OSError, ValueError, StopIteration):
        return result
    result["cgroupMemoryBytes"] = integer_file(path / "memory.current")
    result["cgroupIo"] = cgroup_io(path)
    try:
        for line in (path / "cpu.stat").read_text().splitlines():
            key, value = line.split()
            if key == "usage_usec":
                result["cgroupCpuUsec"] = int(value)
    except (OSError, ValueError):
        pass
    return result


def host_network():
    rx, tx, count = 0, 0, 0
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, _, values = line.partition(":")
            # Physical host interfaces only; this is not container-attributed traffic.
            if not (Path("/sys/class/net") / name.strip() / "device").exists():
                continue
            values = values.split()
            rx += int(values[0])
            tx += int(values[8])
            count += 1
    except (OSError, ValueError, IndexError):
        return {"hostPhysicalInterfaceCount": 0, "hostRxBytes": None, "hostTxBytes": None}
    return {
        "hostPhysicalInterfaceCount": count,
        "hostRxBytes": rx if count else None,
        "hostTxBytes": tx if count else None,
    }


def sample(compose, service="bmw-mqtt-bridge"):
    row = {
        "schemaVersion": 2,
        "timestampUtc": datetime.now(timezone.utc).isoformat(),
        "status": "unavailable",
    }
    container = command(compose + ["ps", "--all", "-q", service])
    if not container or len(container.splitlines()) != 1:
        return row, None, None
    template = (
        "{{.State.Pid}} {{.RestartCount}} {{.State.Status}} "
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.State.StartedAt}}"
    )
    state = command(["docker", "inspect", "--format", template, container])
    if not state or len(state.split()) != 5:
        return row, None, None
    pid, restarts, status, health, started = state.split()
    if not restarts.isdecimal():
        return row, None, None
    row.update(status=status, health=health, restartCount=int(restarts))
    identity = container, started  # Used internally only; do not save installation IDs.
    if status != "running" or not pid.isdigit() or pid == "0":
        return row, identity, None
    process_list = command(["docker", "top", container, "-eo", "pid"])
    pids = [p.strip() for p in (process_list or "").splitlines()[1:] if p.strip().isdigit()]
    row.update(process_memory(pids))
    io, counters = process_io(pids)
    row.update(io)
    row.update(cgroup_metrics(pid))
    row.update(host_network())
    return row, identity, counters


def cpu_percent(previous, current, elapsed):
    if previous is None or current is None or elapsed <= 0 or current < previous:
        return None
    return (current - previous) / (elapsed * 10000)


def counter_rates(previous, current, elapsed):
    """Do not compare different devices/process lifetimes or reset counters."""
    if previous is None or current is None or elapsed <= 0 or set(previous) != set(current):
        return None
    result = {}
    for identity, values in current.items():
        old = previous[identity]
        if set(old) != set(values) or any(values[key] < old[key] for key in values):
            return None
        result[identity] = {key: (value - old[key]) / elapsed for key, value in values.items()}
    return result


def validate_host(parser):
    """Host /proc PIDs must belong to the daemon whose containers we inspect."""
    if sys.platform != "linux":
        parser.error("run directly on the Linux Docker host")
    endpoint = os.environ.get("DOCKER_HOST")
    if os.environ.get("DOCKER_CONTEXT") or not endpoint:
        endpoint = command([
            "docker", "context", "inspect", "--format",
            '{{(index .Endpoints "docker").Host}}',
        ])
    if not endpoint or not endpoint.startswith("unix://"):
        parser.error("a local Docker Unix socket is required; remote contexts are unsupported")
    if command(["docker", "info", "--format", "{{.OSType}}"]) != "linux":
        parser.error("cannot access the local Linux Docker daemon")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, default=Path("/dev/shm/bmb-resource-soak.jsonl"))
    parser.add_argument("--compose-file", type=str)
    parser.add_argument("--service", default="bmw-mqtt-bridge")
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 86400 or not 1 <= args.interval_seconds <= 3600:
        parser.error("duration must be 1..86400 seconds; interval must be 1..3600 seconds")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", args.service):
        parser.error("service must be a valid Compose service name")
    validate_host(parser)
    compose = ["docker", "compose"]
    if args.compose_file:
        compose += ["-f", args.compose_file]
    services = command(compose + ["config", "--services"])
    if not services or args.service not in services.splitlines():
        parser.error("cannot find the requested service in the Compose project")
    previous_identity, previous_cpu, previous_time, generation = None, None, None, 0
    previous_cgroup_io, previous_process_io = None, None
    started = time.monotonic()
    deadline = started + args.duration_seconds
    # Exclusive creation prevents overwriting previous measurements.
    with args.output.open("x", encoding="utf-8") as output:
        while True:
            row, identity, process_counters = sample(compose, args.service)
            measured = time.monotonic()
            row["elapsedSeconds"] = round(measured - started, 3)
            if identity != previous_identity:
                generation += 1
            row["generation"] = generation
            row["cpuPercentOneCore"] = (
                cpu_percent(previous_cpu, row.get("cgroupCpuUsec"), measured - previous_time)
                if identity is not None
                and identity == previous_identity
                and previous_time is not None
                else None
            )
            same_generation = (
                identity is not None and identity == previous_identity and previous_time is not None
            )
            elapsed = measured - previous_time if same_generation else 0
            row["cgroupIoPerSecond"] = counter_rates(
                previous_cgroup_io, row.get("cgroupIo"), elapsed
            )
            rates = counter_rates(previous_process_io, process_counters, elapsed)
            row["processIoPerSecond"] = (
                {key: sum(values[key] for values in rates.values()) for key in PROCESS_IO_FIELDS}
                if rates
                else None
            )
            output.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
            output.flush()
            previous_identity, previous_cpu, previous_time = (
                identity,
                row.get("cgroupCpuUsec"),
                measured,
            )
            previous_cgroup_io, previous_process_io = row.get("cgroupIo"), process_counters
            if measured >= deadline:
                break
            time.sleep(min(args.interval_seconds, deadline - measured))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as error:
        print(f"Cannot write measurement: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        raise SystemExit(130) from None
