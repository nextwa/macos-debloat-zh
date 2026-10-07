import contextlib
import io
import json
from pathlib import Path
import test_debloat as base


class IntegrationTest(base.FakeMachineTest):
    run_cli = base.CommandLineTest.run_cli
    two_labels = base.CommandLineTest.two_labels

    def setUp(self):
        super().setUp()
        self.debloat.BACKUP_DIR = self.machine.tmp / 'backup'
        self.debloat.PRESETS_DIR = self.debloat.BACKUP_DIR / 'presets'
        self.debloat.USER_LABELS_FILE = self.debloat.BACKUP_DIR / 'labels.txt'
        self.debloat.PRESETS_DIR.mkdir(parents=True)
        self.debloat.mem_free_mb = lambda: 4096
        self.debloat.is_sip_enabled = lambda: True
        self.debloat.sync_boot_daemon = lambda sections: ''

    def test_extreme_restores_airdrop_and_keeps_search(self):
        self.debloat.EMBEDDED_LABELS = (
            'com.apple.sharingd [sip-off]\ncom.apple.campo\n'
            'com.apple.metadata.mds\ncom.example.unused\n'
        )
        for label in ('com.apple.sharingd', 'com.apple.campo', 'com.apple.metadata.mds'):
            self.machine.add(label, agent_plist=True, registered=[self.gui], disabled=[self.gui])
        self.machine.add('com.example.unused', daemon_plist=True, registered=['system'])
        code, _ = self.run_cli('--preset', self.debloat.EXTREME_PRESET)
        self.assertEqual(code, 0)
        self.machine.reread()
        self.assertEqual(self.machine.state['domains'][self.gui]['disabled'], [])
        self.assertEqual(self.machine.state['domains']['system']['disabled'], ['com.example.unused'])

    def test_extreme_preserves_required_dependencies(self):
        for label in ('com.apple.sharingd', 'com.apple.rapportd', 'com.apple.nearbyd',
                      'com.apple.wifip2pd', 'com.apple.identityservicesd', 'com.apple.akd',
                      'com.apple.contactsd', 'com.apple.campo', 'com.apple.corespotlightd',
                      'com.apple.metadata.mds', 'com.apple.watchdogd'):
            self.assertTrue(self.debloat.preserve_reason(label), label)
        self.assertFalse(self.debloat.preserve_reason('com.apple.photoanalysisd'))

    def test_restore_preserves_different_states_in_two_domains_and_snapshot(self):
        label = 'com.example.both'
        self.debloat.EMBEDDED_LABELS = label
        self.machine.add(label, agent_plist=True, daemon_plist=True,
                         registered=['system', self.gui], disabled=['system'])
        self.run_cli('--disable-all')
        saved = self.debloat.BACKUP_DIR / 'latest.json'
        payload = saved.read_text()
        code, _ = self.run_cli('--restore')
        self.assertEqual(code, 0)
        self.machine.reread()
        self.assertEqual(self.machine.state['domains']['system']['disabled'], [label])
        self.assertEqual(self.machine.state['domains'][self.gui]['disabled'], [])
        self.assertEqual(saved.read_text(), payload)

    def test_single_dry_run_is_read_only_and_uses_extreme(self):
        self.two_labels()
        code, out = self.run_cli('--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('[dry-run]', out)
        self.assertEqual(self.machine.commands(), [])
        self.assertFalse((self.debloat.BACKUP_DIR / 'latest.json').exists())

    def test_second_catalog_is_merged_without_duplicate_labels(self):
        module = base.load_debloat()
        labels = [it.label for sec in module.parse_labels(module.EMBEDDED_LABELS) for it in sec.items]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertIn('com.apple.Passwords', labels)
        self.assertIn('com.apple.contextstored', labels)

    def test_retry_reports_already_disabled_running_service(self):
        label = 'com.example.already-disabled'
        self.machine.add(label, daemon_plist=True, registered=['system'], disabled=['system'], pid=998877)
        self.machine.fail('bootout', 150, 'System Integrity Protection is engaged')
        secs, _ = self.sections(label)
        self.debloat.refresh_state(secs)
        # Exercise launchd state and retry selection without sending a process signal.
        result = self.debloat.apply_changes(secs, retry_running=True, stop_processes=False)
        self.assertEqual(result['disabled'], 1)
        self.assertIn(label, result['stragglers'])

    def test_partial_failure_has_nonzero_exit_code(self):
        self.two_labels()
        self.machine.swallow_overrides()
        code, _ = self.run_cli('--disable-all')
        self.assertEqual(code, 2)


if __name__ == '__main__':
    import unittest
    unittest.main()
