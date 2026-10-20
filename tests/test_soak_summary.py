"""Protect summary attribution, missing-value handling and restart boundaries."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('summarize_soak', Path(__file__).with_name('summarize_soak.py'))
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)

COLLECTOR_SPEC = importlib.util.spec_from_file_location('resource_soak', Path(__file__).with_name('resource_soak.py'))
collector = importlib.util.module_from_spec(COLLECTOR_SPEC)
COLLECTOR_SPEC.loader.exec_module(collector)


def row(elapsed, generation=1, **values):
    return dict(schemaVersion=2, elapsedSeconds=elapsed, generation=generation,
                status='running', health='healthy', composeService='bmw-mqtt-bridge',
                bridgeVersion='fixture', **values)


def report(*rows):
    return summary.summarize(json.dumps(value) for value in rows)


class SoakSummaryTests(unittest.TestCase):
    def test_initial_unavailable_samples_from_collector_are_summarized(self):
        observations = [
            ({'schemaVersion': 2, 'status': 'unavailable'}, None, None),
            ({'schemaVersion': 2, 'status': 'unavailable'}, None, None),
            ({'schemaVersion': 2, 'status': 'running', 'health': 'healthy', 'cgroupCpuUsec': 100000},
             ('private-container', 'first-start'), None),
            ({'schemaVersion': 2, 'status': 'running', 'health': 'healthy', 'cgroupCpuUsec': 700000},
             ('private-container', 'first-start'), None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'samples.jsonl'
            with (
                patch('sys.argv', ['resource_soak', '--duration-seconds', '180', '--output', str(output)]),
                patch.object(collector, 'validate_host'),
                patch.object(collector, 'command', return_value='bmw-mqtt-bridge'),
                patch.object(collector, 'sample', side_effect=observations),
                patch.object(collector.time, 'monotonic', side_effect=[0, 0, 60, 120, 180]),
                patch.object(collector.time, 'sleep'),
            ):
                self.assertEqual(collector.main(), 0)
            with output.open() as source:
                result = summary.summarize(source)
        self.assertEqual(result['samples'], 4)
        self.assertEqual(result['statusSamples'], {'unavailable': 2, 'running': 2})
        self.assertEqual([segment['generation'] for segment in result['segments']], [0, 1])
        missing, running = result['segments']
        self.assertEqual(missing['samples'], 2)
        self.assertEqual(missing['cpuCoverageSeconds'], 0)
        self.assertIsNone(missing['cpuMeanPercentOneCore'])
        self.assertIsNone(missing['processWriteBytesApprox'])
        self.assertEqual(running['cpuCoverageSeconds'], 60)
        self.assertEqual(running['cpuMeanPercentOneCore'], 1)

    def test_entirely_unavailable_run_keeps_missing_metrics_unknown(self):
        result = report(*[
            dict(schemaVersion=2, elapsedSeconds=elapsed, generation=0, status='unavailable')
            for elapsed in (0, 60, 120)
        ])
        self.assertEqual(result['statusSamples'], {'unavailable': 3})
        self.assertEqual(result['spanSeconds'], 120)
        segment = result['segments'][0]
        self.assertEqual(segment['generation'], 0)
        self.assertEqual(segment['durationSeconds'], 120)
        for key in ('cpuMeanPercentOneCore', 'processWriteBytesApprox', 'rssMaxBytes'):
            self.assertIsNone(segment[key])

    def test_weighted_cpu_memory_and_write_totals_have_explicit_coverage(self):
        result = report(
            row(0, restartCount=2, processRssSumBytes=100, processesExpected=1, processesRead=1),
            row(60, cpuPercentOneCore=1, processRssSumBytes=120, processesExpected=1, processesRead=1,
                processIoPerSecond={'write_bytes': 2}),
            row(180, cpuPercentOneCore=4, processRssSumBytes=110, processesExpected=1, processesRead=1,
                processIoPerSecond={'write_bytes': 1}),
        )
        segment = result['segments'][0]
        self.assertEqual(result['composeServices'], ['bmw-mqtt-bridge'])
        self.assertEqual(segment['cpuMeanPercentOneCore'], 3)
        self.assertEqual(segment['cpuCoverageSeconds'], 180)
        self.assertEqual(segment['restartCountMax'], 2)
        self.assertEqual(segment['rssMaxBytes'], 120)
        self.assertEqual(segment['rssChangeBytes'], 10)
        self.assertEqual(segment['processWriteBytesApprox'], 240)
        self.assertEqual(segment['processWriteCoverageSeconds'], 180)

    def test_generations_and_missing_metrics_do_not_imply_zero_or_memory_leaks(self):
        result = report(row(0), row(60, 2, cpuPercentOneCore=99, processIoPerSecond={'write_bytes': 999}),
                        row(120, 2, processRssSumBytes=100, processesExpected=2, processesRead=1))
        for segment in result['segments']:
            self.assertIsNone(segment['cpuMeanPercentOneCore'])
            self.assertNotIn('cpuWeightedTotal', segment)
            self.assertIsNone(segment['processWriteBytesApprox'])
            self.assertIsNone(segment['rssMaxBytes'])
            self.assertIsNone(segment['rssChangeBytes'])
        self.assertEqual(len(result['segments']), 2)
        self.assertEqual(report(row(0), row(60, processIoPerSecond={'write_bytes': 0}))
                         ['segments'][0]['processWriteBytesApprox'], 0)

    def test_bad_empty_nonfinite_and_out_of_order_input_fails(self):
        for values in [[], ['broken'], ['null'], [json.dumps(row(float('nan')))],
                       [json.dumps(row(0, -1))], [json.dumps(row(0, True))],
                       [json.dumps(row(0, 0.5))],
                       [json.dumps(row(0)), json.dumps(row(0))],
                       [json.dumps(row(1, 2)), json.dumps(row(2, 1))]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                summary.summarize(values)

    def test_old_files_are_explicitly_unattributed(self):
        legacy = row(0)
        del legacy['composeService']
        del legacy['bridgeVersion']
        result = report(legacy)
        self.assertEqual(result['composeServices'], ['unknown'])
        self.assertEqual(result['bridgeVersions'], ['unknown'])
