import io
import contextlib
import json
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import memory_tools as memory
import debloat
from test_workflow import Screen


def process(pid, parent, name, bundle=None, footprint=1000, start=1):
    return dict(pid=pid, parent_pid=parent, name=name, bundle=bundle,
                footprint_bytes=footprint, rss_bytes=2000, start_time=start)


def sample(pressure=1):
    rows = [process(10, 1, 'Editor', '/Applications/Editor.app')]
    return {
        'recorded_at': '2026-10-08T20:00:00+08:00',
        'memory': dict(physical_memory_bytes=16 * 1024**3, pressure_level=pressure,
                       pressure=memory.PRESSURE_NAMES[pressure], compressor_bytes=100 * memory.MIB,
                       wired_bytes=200 * memory.MIB, file_backed_bytes=300 * memory.MIB,
                       free_and_speculative_bytes=400 * memory.MIB, swap_used_bytes=0,
                       swapin_bytes_total=0, swapout_bytes_total=0),
        'process_count': len(rows), 'measured_count': len(rows),
        'processes': rows, 'groups': memory.group_processes(rows),
    }


class MemoryTest(unittest.TestCase):
    def test_apple_silicon_page_size_and_swap_units(self):
        text = '''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 10.
Pages speculative: 2.
Pages occupied by compressor: 20.
Pages wired down: 30.
File-backed pages: 40.
Swapins: 3.
Swapouts: 4.
'''
        result = memory.parse_memory(text, 'total = 1.00G used = 256.50M free = 0.00M', 16 * 1024**3, 2)
        self.assertEqual(result['compressor_bytes'], 20 * 16384)
        self.assertEqual(result['free_and_speculative_bytes'], 12 * 16384)
        self.assertEqual(result['swapout_bytes_total'], 4 * 16384)
        self.assertEqual(result['swap_used_bytes'], int(256.5 * memory.MIB))
        self.assertEqual(result['pressure'], '偏高')

    def test_application_helpers_group_without_counting_unreadable_footprints_as_zero(self):
        rows = [process(10, 1, 'Editor', '/Applications/Editor.app'),
                process(11, 10, 'python'), process(12, 11, 'worker', footprint=None),
                process(20, 1, 'Browser', '/Applications/Browser.app'),
                process(21, 1, 'Browser Helper', '/Applications/Browser.app'),
                process(30, 1, 'system', footprint=None)]
        groups = {g['name']: g for g in memory.group_processes(rows)}
        self.assertEqual(groups['Editor']['pids'], [10, 11, 12])
        self.assertEqual(groups['Editor']['measured_count'], 2)
        self.assertFalse(groups['Editor']['complete'])
        self.assertTrue(memory.footprint_text(groups['Editor']).startswith('≥'))
        self.assertEqual(groups['Browser']['footprint_bytes'], 2000)
        self.assertIsNone(groups['system']['footprint_bytes'])

    def test_comparison_keeps_negative_results_and_excludes_restarted_processes(self):
        before, after = sample(), sample()
        before['processes'].append(process(20, 1, 'Other', footprint=8000, start=1))
        after['processes'].append(process(20, 1, 'Other', footprint=3000, start=2))
        after['processes'][0]['footprint_bytes'] = 2500
        after['memory']['compressor_bytes'] += memory.MIB
        after['memory']['swapout_bytes_total'] = 2 * memory.MIB
        delta = memory.compare(before, after)
        self.assertEqual(delta['stable_process_count'], 1)
        self.assertEqual(delta['stable_footprint_delta_bytes'], 1500)
        self.assertEqual(delta['memory_delta']['compressor_bytes'], memory.MIB)
        self.assertEqual(delta['swapout_bytes'], 2 * memory.MIB)

    def test_reclaim_is_simulated_warning_and_records_observation(self):
        before, after = sample(), sample()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory.os, 'geteuid', return_value=501), \
             patch.object(memory.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
             patch.object(memory, 'capture', return_value=after), patch.object(memory.time, 'sleep'):
            result = memory.reclaim(Path(directory), before=before)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', '/usr/bin/memory_pressure', '-S', '-l', 'warn', '-s', '1'])
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(json.loads((Path(directory) / 'last-memory.json').read_text())['after'], after)

    def test_rejected_request_never_claims_reclamation(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'permission denied')), \
             patch.object(memory, 'capture', side_effect=AssertionError('sampled after failed command')):
            result = memory.reclaim(Path(directory), before=sample())
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('comparison', result)
            self.assertIn('permission denied', '\n'.join(memory.report_lines(result)))

    def test_critical_pressure_skips_request(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory.subprocess, 'run', side_effect=AssertionError('pressure request sent')):
            result = memory.reclaim(Path(directory), before=sample(4))
            self.assertEqual(result['status'], 'skipped')

    def test_interrupt_is_deferred_until_pressure_command_finishes(self):
        original = signal.getsignal(signal.SIGINT)
        def finish(*args, **kwargs):
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            return subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory.subprocess, 'run', side_effect=finish), \
             patch.object(memory, 'capture', return_value=sample()), patch.object(memory.time, 'sleep'):
            memory.reclaim(Path(directory), before=sample())
        self.assertEqual(signal.getsignal(signal.SIGINT), original)

    def test_cli_preview_and_status_do_not_request_admin_or_load_service_catalog(self):
        with patch.object(memory, 'capture', return_value=sample()), \
             patch.object(debloat, 'prime_sudo', side_effect=AssertionError('sudo called')), \
             patch.object(debloat, 'load_sections', side_effect=AssertionError('service catalog read')):
            for argv in (['--memory', '--json'], ['--reclaim-memory', '--dry-run', '--json']):
                output = io.StringIO()
                with patch.object(sys, 'argv', ['debloat.py', *argv]), contextlib.redirect_stdout(output):
                    self.assertEqual(debloat.main(), 0)
                self.assertIsInstance(json.loads(output.getvalue()), dict)

    def test_cli_does_not_combine_reclaim_and_service_mutation(self):
        with contextlib.redirect_stderr(io.StringIO()), \
             patch.object(memory, 'capture', side_effect=AssertionError('capture called')):
            self.assertEqual(debloat.cmd_memory(['--reclaim-memory', '--disable-all']), 1)

    def test_memory_panel_cancel_and_narrow_layout(self):
        for size in ((24, 80), (35, 120), (16, 60)):
            screen = Screen(debloat, keys=[ord('o'), ord('n'), ord('q')], size=size)
            with patch.object(memory, 'capture', return_value=sample()), \
                 patch.object(debloat, 'authenticate', side_effect=AssertionError('sudo after cancel')):
                debloat.run_memory_tui(screen)
            self.assertIn('应用缓存回收预览', '\n'.join(screen.written))

    def test_memory_panel_confirm_shows_actual_result(self):
        before, after = sample(), sample()
        report = dict(status='completed', before=before, after=after, comparison=memory.compare(before, after))
        screen = Screen(debloat, keys=[ord('o'), ord('y'), 10, ord('q')])
        with patch.object(memory, 'capture', return_value=before), \
             patch.object(debloat, 'authenticate', return_value=True), \
             patch.object(memory, 'reclaim', return_value=report) as reclaim:
            debloat.run_memory_tui(screen)
        reclaim.assert_called_once()
        self.assertIn('缓存回收结果', '\n'.join(screen.written))


if __name__ == '__main__':
    unittest.main()
