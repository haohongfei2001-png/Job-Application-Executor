"""Bounded diagnostic branch, no PR trigger, privileged token or publication."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'.github/workflows/preparation-research.yml').read_text()


class PreparationResearchWorkflowTests(unittest.TestCase):
    def test_branch_scope_permissions_budget_and_outputs(self):
        self.assertIn('branches: [test/jae-preparation-research]',SOURCE)
        self.assertIn('permissions:\n  contents: read\n',SOURCE)
        self.assertIn('persist-credentials: false',SOURCE)
        self.assertIn('timeout-minutes: 12',SOURCE)
        self.assertIn('os: [ubuntu-latest, macos-latest]',SOURCE)
        self.assertIn('timeout-minutes: 2',SOURCE)
        self.assertIn('scripts/probe_qiyunfang_preflight.py',SOURCE)
        self.assertIn('tests/test_preparation_public_reads_v1.py',SOURCE)
        self.assertIn('tests/test_preparation_discovery_v1.py',SOURCE)
        self.assertIn('tests/test_preparation_preflight_v1.py',SOURCE)
        self.assertIn('tests/test_preparation_authority_browser.py',SOURCE)
        self.assertIn('tests/test_preparation_final_journal_v1.py',SOURCE)
        self.assertIn('tests/test_preparation_process_identity_browser.py',SOURCE)
        self.assertIn('tests/test_preparation_flow_v1.py',SOURCE)
        self.assertIn('tests/test_preparation_flow_browser.py',SOURCE)
        self.assertIn('retention-days: 3',SOURCE)
        for forbidden in ('pull_request','workflow_dispatch','secrets.','write-all','contents: write',
                          'build_macos_app','upload-release','gh pr','curl ','profiles/',
                          'continue-on-error','test_macos_host_v1.py','--ignore-certificate-errors'):
            self.assertNotIn(forbidden,SOURCE)


if __name__=='__main__':unittest.main()
