import contextlib
import io
import json
import subprocess
from unittest.mock import patch

import test_debloat as base


class WorkflowTest(base.FakeMachineTest):
    run_cli = base.CommandLineTest.run_cli

    def setUp(self):
        super().setUp()
        self.debloat.is_sip_enabled = lambda: True
        self.debloat.PRESETS_DIR = self.machine.tmp / 'presets'
        self.debloat.USER_LABELS_FILE = self.machine.tmp / 'labels.txt'

    def test_partial_domain_enable_clears_remaining_override(self):
        label = 'com.example.both'
        self.machine.add(label, daemon_plist=True, agent_plist=True,
                         registered=['system', self.gui], disabled=['system'])
        secs, _ = self.sections(label)
        self.debloat.refresh_state(secs)
        self.assertEqual(self.debloat.service_status(secs[0].items[0]), 'partial-disabled')
        self.assertEqual(self.debloat.pending_changes(secs), ([], []))
        secs[0].items[0].selected = True
        self.assertEqual(self.debloat.pending_changes(secs), ([], [label]))
        self.debloat.apply_changes(secs)
        self.machine.reread()
        self.assertNotIn(label, self.machine.state['domains']['system']['disabled'])

    def test_sip_refusal_is_partial_even_when_job_is_idle(self):
        label = 'com.example.protected'
        self.debloat.EMBEDDED_LABELS = label
        self.machine.add(label, daemon_plist=True, registered=['system'])
        self.machine.fail('bootout', 150, 'System Integrity Protection is engaged')
        with patch.object(self.debloat, 'sync_boot_daemon', return_value=''):
            code, _ = self.run_cli('--disable-all')
        self.assertEqual(code, 2)
        result = json.loads((self.debloat.BACKUP_DIR / 'last-apply.json').read_text())
        self.assertEqual(result['sip_blocked'], [label])
        self.assertEqual(result['bootout_errors'][0]['domain'], 'system')
        self.assertIn('SIP 拒绝卸载', self.debloat.apply_problems(result))

    def test_already_unloaded_job_is_not_reported_as_a_failure(self):
        label = 'com.example.absent'
        self.machine.add(label, daemon_plist=True)
        self.machine.fail('bootout', 3, 'No such process')
        secs, _ = self.sections(label)
        self.debloat.refresh_state(secs)
        secs[0].items[0].selected = False
        result = self.debloat.apply_changes(secs)
        self.assertFalse(self.debloat.apply_incomplete(result))
        self.assertEqual(result['stopped'], [label])

    def test_preserved_service_registration_failure_is_visible(self):
        label = 'com.apple.sharingd'
        self.machine.add(label, agent_plist=True, disabled=[self.gui])
        self.machine.fail('bootstrap', 5, 'Input/output error')
        secs, _ = self.sections(label)
        self.debloat.refresh_state(secs)
        secs[0].items[0].selected = True
        result = self.debloat.apply_changes(secs)
        self.assertEqual(result['not_enabled'], [])
        self.assertEqual(result['bootstrap_errors'][0]['label'], label)
        self.assertTrue(self.debloat.apply_incomplete(result))

    def test_boot_reapply_restores_only_explicit_preserved_services(self):
        self.machine.add('com.apple.Siri.agent', agent_plist=True,
                         registered=[self.gui], disabled=[self.gui])
        self.machine.add('com.example.off', daemon_plist=True, registered=['system'])
        self.machine.add('com.example.unrelated', daemon_plist=True,
                         registered=['system'], disabled=['system'])
        result = self.debloat.reapply_persisted({'labels': ['com.example.off'],
                                               'enabled': ['com.apple.Siri.agent']})
        self.machine.reread()
        self.assertEqual(self.machine.state['domains'][self.gui]['disabled'], [])
        self.assertEqual(set(self.machine.state['domains']['system']['disabled']),
                         {'com.example.off', 'com.example.unrelated'})
        self.assertEqual(result['not_enabled'], [])
        self.assertFalse(any('unrelated' in c for c in self.machine.commands()))

    def test_boot_reapply_reads_existing_disabled_only_state(self):
        self.machine.add('com.example.off', daemon_plist=True, registered=['system'])
        self.debloat.reapply_persisted({'labels': ['com.example.off']})
        self.machine.reread()
        self.assertIn('com.example.off', self.machine.state['domains']['system']['disabled'])

    def test_existing_daemon_upgrade_is_not_skipped_when_services_are_already_stopped(self):
        label = 'com.example.off'
        self.machine.add(label, daemon_plist=True, disabled=['system'])
        secs, _ = self.sections(label)
        self.debloat.refresh_state(secs)
        self.debloat.PERSIST_DIR.mkdir()
        self.debloat.PERSIST_PLIST.touch()
        self.debloat.PERSIST_STATE.write_text(json.dumps({'uid': self.debloat.UID, 'labels': [label]}))
        with patch.object(self.debloat, 'sync_boot_daemon', return_value='') as sync:
            self.assertEqual(self.debloat.run_apply(secs, False), 0)
            sync.assert_called_once()

    def test_partial_domain_state_outside_preset_is_left_unchanged(self):
        self.machine.add('com.example.both', agent_plist=True, daemon_plist=True, disabled=['system'])
        self.machine.add('com.example.telemetry', daemon_plist=True, registered=['system'])
        secs, _ = self.sections('# === Telemetry / analytics [telemetry] ===', 'com.example.telemetry',
                                '# === Photos ===', 'com.example.both')
        self.debloat.refresh_state(secs)
        self.debloat.select_for_action(secs, 'preset:telemetry')
        self.assertEqual(self.debloat.pending_changes(secs), (['com.example.telemetry'], []))

    def test_dry_run_restore_and_sip_never_reach_mutating_commands(self):
        for action in ('--restore', '--disable-sip', '--enable-sip'):
            with patch.object(self.debloat, 'prime_sudo', side_effect=AssertionError('dry run requested sudo')):
                code, _ = self.run_cli(action, '--dry-run')
            self.assertEqual(code, 1)
        self.assertEqual(self.machine.commands(), [])

    def test_updating_daemon_restarts_it_with_saved_preserved_selection(self):
        self.machine.add('com.example.off', daemon_plist=True, registered=['system'])
        self.machine.add('com.apple.sharingd', agent_plist=True, registered=[self.gui])
        secs, _ = self.sections('com.example.off', 'com.apple.sharingd')
        self.debloat.refresh_state(secs)
        self.items(secs)['com.example.off'].selected = False
        self.debloat.PERSIST_DIR.mkdir()
        self.debloat.PERSIST_PLIST.touch()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.object(self.debloat.subprocess, 'run', side_effect=run), \
             patch.object(self.debloat, 'sudo_write', side_effect=lambda p, t: p.write_text(t)):
            self.debloat.sync_boot_daemon(secs)
        saved = json.loads(self.debloat.PERSIST_STATE.read_text())
        self.assertEqual(saved['enabled'], ['com.apple.sharingd'])
        self.assertEqual(saved['labels'], ['com.example.off'])
        actions = [c[2] for c in calls if c[:2] == ['sudo', 'launchctl']]
        self.assertEqual(actions, ['bootout', 'bootstrap'])

    def test_restore_uses_original_daemon_selection_without_adding_preserved_labels(self):
        label = 'com.apple.sharingd'
        self.machine.add(label, agent_plist=True, registered=[self.gui])
        secs, _ = self.sections(label)
        self.debloat.refresh_state(secs)
        self.debloat.PERSIST_DIR.mkdir()
        prior = {'uid': self.debloat.UID, 'labels': [label]}
        self.debloat.PERSIST_STATE.write_text(json.dumps(prior))
        self.debloat.write_snapshot(secs)
        with patch.object(self.debloat, 'sync_boot_daemon', return_value='') as sync:
            self.assertEqual(self.debloat.cmd_restore(secs), 0)
            self.assertEqual(sync.call_args.kwargs['saved_state'], prior)

    def test_status_separates_running_stopped_and_partial_domains(self):
        self.machine.add('com.example.alive', daemon_plist=True, registered=['system'], disabled=['system'], pid=901)
        self.machine.add('com.example.off', daemon_plist=True, disabled=['system'])
        self.machine.add('com.example.both', daemon_plist=True, agent_plist=True, disabled=['system'])
        secs, _ = self.sections('com.example.alive', 'com.example.off', 'com.example.both')
        data = json.loads(self.capture(self.debloat.cmd_status, secs, True))
        self.assertEqual(data['disabled_but_running'], ['com.example.alive'])
        self.assertEqual(data['disabled_stopped'], ['com.example.off'])
        self.assertEqual(data['partial_disabled'], ['com.example.both'])
        self.assertEqual(data['services'][0]['pids'], [901])

    def test_preview_cancel_never_requests_sudo_or_changes_services(self):
        label = 'com.example.telemetry'
        self.machine.add(label, daemon_plist=True, registered=['system'])
        secs, _ = self.sections('# === Telemetry / analytics [telemetry] ===', label)
        self.debloat.refresh_state(secs)
        # Move to telemetry, stage it, open preview, cancel, quit.
        screen = Screen(self.debloat, keys=[ord('j'), ord('j'), 10, ord('a'), ord('n'), ord('q')])
        with patch.object(self.debloat.curses, 'curs_set'), \
             patch.object(self.debloat, 'prime_sudo', side_effect=AssertionError('preview requested sudo')):
            self.debloat.run_tui(screen, secs)
        self.assertIn('执行预览', '\n'.join(screen.written))
        self.assertEqual(self.machine.commands(), [])
        self.assertFalse((self.debloat.BACKUP_DIR / 'latest.json').exists())

    def test_confirmed_ui_apply_saves_result_and_can_reopen_it(self):
        label = 'com.example.telemetry'
        self.machine.add(label, daemon_plist=True, registered=['system'])
        secs, _ = self.sections('# === Telemetry / analytics [telemetry] ===', label)
        self.debloat.refresh_state(secs)
        screen = Screen(self.debloat, keys=[ord('j'), ord('j'), 10, ord('a'), ord('y'), 10,
                                           ord('v'), 10, ord('q')])
        with patch.object(self.debloat.curses, 'curs_set'), \
             patch.object(self.debloat, 'authenticate', return_value=True), \
             patch.object(self.debloat, 'sync_boot_daemon', return_value=''):
            self.debloat.run_tui(screen, secs)
        result = json.loads((self.debloat.BACKUP_DIR / 'last-apply.json').read_text())
        self.assertEqual(result['stopped'], [label])
        self.assertIn('最近执行结果', '\n'.join(screen.written))
        self.assertTrue((self.debloat.BACKUP_DIR / 'latest.json').exists())

    def test_daemon_start_failure_returns_partial_result(self):
        label = 'com.example.off'
        self.debloat.EMBEDDED_LABELS = label
        self.machine.add(label, daemon_plist=True, registered=['system'])
        with patch.object(self.debloat, 'sync_boot_daemon', side_effect=OSError('cannot start daemon')):
            code, _ = self.run_cli('--disable-all')
        self.assertEqual(code, 2)
        result = json.loads((self.debloat.BACKUP_DIR / 'last-apply.json').read_text())
        self.assertIn('cannot start daemon', self.debloat.apply_problems(result))

    def test_chinese_search_and_empty_results_render_in_standard_terminal(self):
        self.machine.add('com.example.telemetry', daemon_plist=True, registered=['system'])
        secs, _ = self.sections('# === Telemetry / analytics [telemetry] ===', 'com.example.telemetry')
        self.debloat.refresh_state(secs)
        found = self.debloat.filtered_sections(secs, '统计', 0)
        self.assertEqual(found[0].items[0].label, 'com.example.telemetry')
        for width, height in ((80, 24), (120, 35), (60, 16)):
            screen = Screen(self.debloat, size=(height, width))
            self.debloat.draw(screen, [self.debloat.menu_section(secs), *secs], 0, '', [0], all_sections=secs)
            self.debloat.draw(screen, [], 0, '', [0], all_sections=secs, query='无匹配')


class Screen:
    def __init__(self, module, keys=(), size=(24, 80)):
        self.module, self.keys, self.size = module, iter(keys), size
        self.written = []
    def getmaxyx(self):
        return self.size
    def addstr(self, y, x, text, attr=0):
        assert 0 <= y < self.size[0]
        assert x + self.module.display_width(text) < self.size[1], text
        self.written.append(text)
    def getch(self):
        return next(self.keys)
    def erase(self): pass
    def move(self, y, x): pass
    def clrtoeol(self): pass
    def refresh(self): pass
    def timeout(self, value): pass
