#!/usr/bin/env python3
"""Summarize JSONL resource samples without reading credentials or contacting Docker."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def summarize(lines):
    statuses, health, services, versions = Counter(), Counter(), set(), set()
    segments = []
    previous = None
    first_timestamp = last_timestamp = None
    first_elapsed = 0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get('schemaVersion') != 2:
                raise ValueError
            elapsed, generation = row.get('elapsedSeconds'), row.get('generation')
            # The collector stays at generation 0 until it first observes a container.
            if not number(elapsed) or not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
                raise ValueError
            for name in ('status', 'health', 'composeService', 'bridgeVersion'):
                if name in row and not isinstance(row[name], str):
                    raise ValueError
            if row.get('processIoPerSecond') is not None and not isinstance(row['processIoPerSecond'], dict):
                raise ValueError
            if previous and (elapsed <= previous['elapsedSeconds'] or generation < previous['generation']):
                raise ValueError
        except (ValueError, TypeError) as error:
            raise ValueError(f'Invalid or out-of-order sample on line {line_number}') from error
        if previous is None:
            first_elapsed = elapsed
            first_timestamp = row.get('timestampUtc')
        last_timestamp = row.get('timestampUtc')
        statuses[row.get('status', 'unknown')] += 1
        health[row.get('health', 'unknown')] += 1
        services.add(str(row.get('composeService', 'unknown')))
        versions.add(str(row.get('bridgeVersion', 'unknown')))
        if not previous or generation != previous['generation']:
            segments.append({'generation': generation, 'samples': 0, 'durationSeconds': 0,
                             'cpuCoverageSeconds': 0, 'cpuWeightedTotal': 0, 'cpuMaxPercentOneCore': None,
                             'restartCountMax': None, 'rssSamples': 0, 'rssFirstBytes': None, 'rssLastBytes': None, 'rssMaxBytes': None,
                             'processWriteCoverageSeconds': 0, 'processWriteBytesApprox': None})
        segment = segments[-1]
        segment['samples'] += 1
        same_generation = previous and generation == previous['generation']
        duration = elapsed - previous['elapsedSeconds'] if same_generation else 0
        segment['durationSeconds'] += duration
        cpu = row.get('cpuPercentOneCore')
        if duration > 0 and number(cpu):
            segment['cpuCoverageSeconds'] += duration
            segment['cpuWeightedTotal'] += cpu * duration
            segment['cpuMaxPercentOneCore'] = max(cpu, segment['cpuMaxPercentOneCore'] or 0)
        restarts = row.get('restartCount')
        if isinstance(restarts, int) and not isinstance(restarts, bool) and restarts >= 0:
            segment['restartCountMax'] = max(restarts, segment['restartCountMax'] or 0)
        rss = row.get('processRssSumBytes')
        complete = (number(row.get('processesExpected')) and row['processesExpected'] > 0 and
                    row.get('processesRead') == row.get('processesExpected'))
        if number(rss) and complete:
            segment['rssSamples'] += 1
            if segment['rssFirstBytes'] is None:
                segment['rssFirstBytes'] = rss
            segment['rssLastBytes'] = rss
            segment['rssMaxBytes'] = max(rss, segment['rssMaxBytes'] or 0)
        writes = (row.get('processIoPerSecond') or {}).get('write_bytes')
        if duration > 0 and number(writes):
            segment['processWriteCoverageSeconds'] += duration
            segment['processWriteBytesApprox'] = (segment['processWriteBytesApprox'] or 0) + writes * duration
        previous = row
    if previous is None:
        raise ValueError('No samples found')
    for segment in segments:
        coverage = segment['cpuCoverageSeconds']
        total = segment.pop('cpuWeightedTotal')
        segment['cpuMeanPercentOneCore'] = total / coverage if coverage else None
        segment['rssChangeBytes'] = (segment['rssLastBytes'] - segment['rssFirstBytes']
                                     if segment['rssSamples'] > 1 else None)
    return {'samples': sum(statuses.values()), 'startUtc': first_timestamp, 'endUtc': last_timestamp,
            'spanSeconds': previous['elapsedSeconds'] - first_elapsed,
            'composeServices': sorted(services), 'bridgeVersions': sorted(versions),
            'statusSamples': dict(statuses), 'healthSamples': dict(health), 'segments': segments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    try:
        with args.input.open(encoding='utf-8') as source:
            result = summarize(source)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError) as error:
        print(f'Cannot summarize measurement: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
