"""Stdlib-only routing and pre-execution checks for the opt-in closure lane.

The expression evaluator reads the actual job conditions. The guard tests run
both actual inline shell blocks against local Git repositories, never a network,
application runtime, private profile, or hosted build. Run with unittest or pytest.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/application-executor-ci.yml"
SOURCE = WORKFLOW.read_text(encoding="utf-8")
JOBS = dict(re.findall(
    r"^  ([a-z_]+):\n(.*?)(?=^  [a-z_]+:\n|\Z)",
    SOURCE.split("\njobs:\n", 1)[1], re.MULTILINE | re.DOTALL,
))
ORDINARY = {"foundation", "packaged_candidate", "macos_consumer_release", "preparation_validation"}
CLOSURE = ("test", "engineering_closure_macos")
SHA = "1234567890abcdef1234567890abcdef12345678"
OTHER_SHA = "abcdef1234567890abcdef1234567890abcdef1234"
REPOSITORY = "example/jae"
STATIC_STEP = (
    "      - name: Validate exact-head engineering closure routing\n"
    "        run: python3 -m unittest discover -s tests -p test_engineering_closure_workflow_v1.py\n"
)


PREPARATION_STEP = '      - name: Anonymous preparation kernel and controller-death oracle\n        env:\n          APPLICATION_EXECUTOR_BROWSER_MODE: isolated\n          JAE_UI_SCREENSHOT_DIR: ${{ runner.temp }}/jae-preparation-ui\n        run: python -m pytest -q -s tests/test_qiyunfang_preparation_v1.py tests/test_preparation_review_v1.py tests/test_preparation_authority_v1.py tests/test_preparation_authority_browser.py tests/test_preparation_review_browser.py tests/test_qiyunfang_preparation_browser.py tests/test_preparation_human_request_v1.py tests/test_preparation_human_request_browser.py tests/test_preparation_network_fence_v1.py tests/test_preparation_network_fence_browser.py tests/test_preparation_lifecycle_browser.py\n'
PREPARATION_MAC_STEP = "      - name: Anonymous preparation early Mac oracle\n        if: matrix.suite == 'native_integration'\n        env:\n          APPLICATION_EXECUTOR_BROWSER_MODE: isolated\n        run: python -m pytest -q -s tests/test_qiyunfang_preparation_v1.py tests/test_preparation_review_v1.py tests/test_preparation_authority_v1.py tests/test_preparation_authority_browser.py tests/test_preparation_review_browser.py tests/test_qiyunfang_preparation_browser.py tests/test_preparation_human_request_v1.py tests/test_preparation_human_request_browser.py tests/test_preparation_network_fence_v1.py tests/test_preparation_network_fence_browser.py tests/test_preparation_lifecycle_browser.py\n"


PUBLIC_PROBE_STEP = '      - name: Observe public Qiyunfang preflight without applicant data\n        timeout-minutes: 2\n        run: python scripts/probe_qiyunfang_preflight.py --output "$RUNNER_TEMP/jae-qiyunfang-public-preflight.json"\n'
PUBLIC_PROBE_ARTIFACT = '      - name: Retain value-free public preflight diagnostic\n        if: always()\n        uses: actions/upload-artifact@v4\n        with:\n          name: jae-qiyunfang-public-preflight-${{ github.run_id }}-${{ github.run_attempt }}\n          path: ${{ runner.temp }}/jae-qiyunfang-public-preflight.json\n          if-no-files-found: ignore\n          retention-days: 3\n'

def event(sha=SHA, label=None, *, draft=True, fork=False, action="labeled"):
    return {
        "action": action,
        "label": {"name": "eng-" + sha if label is None else label},
        "pull_request": {
            "draft": draft,
            "head": {"sha": sha, "ref": "draft/closure",
                     "repo": {"full_name": "outsider/jae" if fork else REPOSITORY}},
        },
    }


def job_condition(name):
    return re.search(r"^    if: (.+)$", JOBS[name], re.MULTILINE).group(1)


def selected(name, payload, *, event_name="pull_request", result="success", tested_sha=None):
    """Evaluate exactly the Actions-expression subset used by these job guards.

    GitHub string equality is case insensitive. Case-sensitive label/SHA grammar
    is deliberately checked again by the isolated Python guard before execution.
    Unknown syntax fails instead of silently inventing different routing semantics.
    """
    head = payload.get("pull_request", {}).get("head", {}).get("sha", "")
    context = {
        "github": {"event": payload, "event_name": event_name, "repository": REPOSITORY},
        "needs": {"test": {"result": result, "outputs": {
            "closure_head": head if tested_sha is None else tested_sha,
        }}},
        "true": True, "false": False,
    }
    expression = job_condition(name).replace("&&", " and ").replace("||", " or ")

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return context[node.id]
        if isinstance(node, ast.Attribute):
            parent = evaluate(node.value)
            return parent.get(node.attr) if isinstance(parent, dict) else None
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(evaluate(value) for value in node.values)
            if isinstance(node.op, ast.Or):
                return any(evaluate(value) for value in node.values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = evaluate(node.left), evaluate(node.comparators[0])
            if isinstance(left, str) and isinstance(right, str):
                left, right = left.casefold(), right.casefold()
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "format" and not node.keywords):
            template, *args = [evaluate(arg) for arg in node.args]
            return template.format(*args)
        raise AssertionError("unsupported actual workflow condition: " + ast.dump(node))

    return bool(evaluate(ast.parse(expression, mode="eval").body))


def guard_shell(name):
    step = JOBS[name].split(
        "      - name: Refuse stale or ineligible engineering closure head\n", 1,
    )[1].split("      - uses: actions/setup-python@v5\n", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


class EngineeringClosureRoutingTests(unittest.TestCase):
    def assert_jobs(self, payload, expected, **kwargs):
        actual = {name for name in JOBS if selected(name, payload, **kwargs)}
        self.assertEqual(actual, set(expected))

    def test_matching_exact_head_label_only_selects_full_suite_then_single_build(self):
        for draft in (True, False):
            with self.subTest(draft=draft):
                payload = event(draft=draft)
                before = copy.deepcopy(payload)
                self.assert_jobs(payload, CLOSURE)
                self.assertEqual(payload, before)  # Draft state is never changed.

    def test_wrong_malformed_old_and_unrelated_labels_do_not_select_any_work(self):
        for draft in (True, False):
            for label in ("bug", "eng-", "eng-" + OTHER_SHA, "eng-" + SHA[:-1],
                          "eng-" + SHA + "0", "eng-" + "z" * 40,
                          "eng-" + SHA + "\n", "prefix-eng-" + SHA,
                          "eng-" + SHA + ";echo unexpected"):
                with self.subTest(draft=draft, label=label):
                    self.assert_jobs(event(label=label, draft=draft), ())

    def test_fork_label_never_selects_any_work_even_when_not_draft(self):
        for draft in (True, False):
            with self.subTest(draft=draft):
                self.assert_jobs(event(draft=draft, fork=True), ())

    def test_normal_push_and_pr_policies_are_preserved(self):
        self.assert_jobs({}, {"foundation", "macos_consumer_release", "test", "preparation_validation"}, event_name="push")
        for action in ("opened", "synchronize", "reopened", "ready_for_review"):
            for draft in (True, False):
                for fork in (True, False):
                    with self.subTest(action=action, draft=draft, fork=fork):
                        expected = ORDINARY if draft else {"foundation", "macos_consumer_release", "test", "preparation_validation"}
                        self.assert_jobs(event(action=action, draft=draft, fork=fork), expected)

    def test_build_requires_success_and_same_tested_head(self):
        for result in ("failure", "cancelled", "skipped", "", "pending"):
            with self.subTest(result=result):
                self.assertFalse(selected("engineering_closure_macos", event(), result=result))
        for head in ("", OTHER_SHA):
            with self.subTest(tested_sha=head):
                self.assertFalse(selected("engineering_closure_macos", event(), tested_sha=head))

    def test_permissions_triggers_budgets_and_artifact_scope_stay_bounded(self):
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review, labeled]", SOURCE)
        self.assertEqual(SOURCE.split("permissions:\n", 1)[1].split("\njobs:", 1)[0], "  contents: read\n")
        self.assertEqual(len(re.findall(r"^\s*permissions:", SOURCE, re.MULTILINE)), 1)
        self.assertEqual(set(JOBS), ORDINARY | set(CLOSURE) | {'qiyunfang_human_channel_smoke'})
        smoke=JOBS['qiyunfang_human_channel_smoke']
        self.assertIn("    if: github.event_name == 'pull_request' && github.event.action != 'labeled' && github.head_ref == 'fix/qiyunfang-human-channel' && github.event.pull_request.head.repo.full_name == github.repository\n",smoke)
        self.assertIn('    timeout-minutes: 8\n',smoke)
        self.assertIn('    runs-on: macos-latest\n',smoke)
        self.assertIn("tests/test_preparation_human_review_browser.py::test_private_human_review_exact_request_or_zero_and_closed_owner[accept]",smoke)
        self.assertNotIn('upload-artifact',smoke)
        self.assertNotIn('needs:',smoke)
        self.assertNotIn('continue-on-error',smoke)
        expected_budgets = {"foundation": 30, "packaged_candidate": 20,
                            "macos_consumer_release": 25, "test": 70,
                            "engineering_closure_macos": 20, "preparation_validation": 25}
        for name, budget in expected_budgets.items():
            self.assertIn(f"    timeout-minutes: {budget}\n", JOBS[name])
        build = JOBS["engineering_closure_macos"]
        self.assertIn("    needs: test\n", build)
        self.assertIn("    runs-on: macos-latest\n", build)
        self.assertNotIn("matrix:", build)
        self.assertNotIn("playwright install", build)
        self.assertIn("run: python -m pip install -r requirements.txt", build)
        self.assertEqual(build.count("scripts/build_macos_app.py"), 1)
        self.assertEqual(build.count("uses: actions/upload-artifact@v4"), 1)
        self.assertIn('python scripts/prepare_standalone_runtime.py "$RUNNER_TEMP/jae-standalone"', build)
        self.assertIn('run: python scripts/build_macos_app.py --standalone-runtime "$JAE_STANDALONE_RUNTIME" --output "$RUNNER_TEMP/jae-unsigned-distribution"', build)
        artifact = build.split("        with:\n", 1)[-1].split("          path: |\n", 1)[1]
        paths = artifact.split("          if-no-files-found:", 1)[0].strip().splitlines()
        self.assertEqual([path.strip() for path in paths], [
            "${{ runner.temp }}/jae-unsigned-distribution/AIApplicationManager-unsigned.tar.gz",
            "${{ runner.temp }}/jae-unsigned-distribution/distribution-manifest.json",
        ])
        self.assertIn("          if-no-files-found: error\n", artifact)
        self.assertIn("          retention-days: 7\n", artifact)
        self.assertIn("jae-engineering-NOT_CERTIFIED-${{ needs.test.outputs.closure_head }}-", build)
        self.assertNotIn("always()", build)
        for forbidden in ("workflow_dispatch", "pull_request_target", "secrets.", "write-all", "codesign ",
                          "notarytool", "gh pr ready", "gh api", "curl ", "trace.zip", "profiles/", "continue-on-error"):
            self.assertNotIn(forbidden, SOURCE)

    def test_preparation_oracle_has_its_own_bounded_allocation(self):
        body=JOBS['preparation_validation']
        self.assertIn('    timeout-minutes: 25\n',body)
        self.assertIn('os: [ubuntu-latest, macos-latest]',body)
        self.assertIn('persist-credentials: false',body)
        for filename in ['tests/test_qiyunfang_preparation_v1.py', 'tests/test_preparation_review_v1.py', 'tests/test_preparation_authority_v1.py', 'tests/test_preparation_final_journal_v1.py', 'tests/test_preparation_process_identity_v1.py', 'tests/test_preparation_public_reads_v1.py', 'tests/test_preparation_discovery_v1.py', 'tests/test_preparation_preflight_v1.py', 'tests/test_preparation_authority_browser.py', 'tests/test_preparation_process_identity_browser.py', 'tests/test_preparation_flow_v1.py', 'tests/test_preparation_flow_browser.py', 'tests/test_preparation_resume_material_v1.py', 'tests/test_preparation_resume_journal_v1.py', 'tests/test_preparation_resume_transport_v1.py', 'tests/test_preparation_resume_flow_v1.py', 'tests/test_preparation_resume_browser.py', 'tests/test_preparation_controller_v1.py', 'tests/test_preparation_session_v1.py', 'tests/test_preparation_session_browser.py', 'tests/test_preparation_review_browser.py', 'tests/test_qiyunfang_preparation_browser.py', 'tests/test_preparation_human_request_v1.py', 'tests/test_preparation_human_request_browser.py', 'tests/test_preparation_network_fence_v1.py', 'tests/test_preparation_network_fence_browser.py', 'tests/test_preparation_lifecycle_browser.py']:
            self.assertEqual(body.count(filename),1)
        self.assertNotIn('tests/test_macos_host_v1.py',body)
        self.assertNotIn('secrets.',body)
        self.assertLess(body.index('Retain value-free public preflight diagnostic'),body.index('Isolated preparation ownership'))

    def test_both_guards_precede_repository_code_and_use_exact_checkout(self):
        self.assertEqual(guard_shell("test"), guard_shell("engineering_closure_macos"))
        for name in CLOSURE:
            body = JOBS[name]
            guard = body.index("      - name: Refuse stale or ineligible engineering closure head\n")
            setup = body.index("      - uses: actions/setup-python@v5\n")
            before_guard = body[body.index("    steps:\n") + len("    steps:\n"):guard]
            self.assertNotIn("run:", before_guard)
            self.assertEqual(re.findall(r"^      - .+$", before_guard, re.MULTILINE),
                             ["      - uses: actions/checkout@v4"])
            self.assertLess(guard, setup)
            self.assertIn("python3 -I - <<'PY'\n", guard_shell(name))
            self.assertIn('git("check-ref-format", full_ref)', guard_shell(name))
            self.assertIn('git("ls-remote", "--exit-code", "--refs", "origin", full_ref)', guard_shell(name))
            self.assertNotIn("shell=True", guard_shell(name))
            self.assertNotIn("${{", guard_shell(name))
        self.assertIn("        id: closure_head\n        if: github.event.action == 'labeled'\n", JOBS["test"])
        self.assertIn("        id: closure_head\n        env:\n", JOBS["engineering_closure_macos"])
        self.assertIn("ref: ${{ github.event.action == 'labeled' && github.event.pull_request.head.sha || '' }}", JOBS["test"])
        self.assertIn("closure_head: ${{ steps.closure_head.outputs.sha }}", JOBS["test"])
        self.assertIn("JAE_EXPECTED_HEAD: ${{ github.event.pull_request.head.sha }}", JOBS["test"])
        self.assertIn("ref: ${{ needs.test.outputs.closure_head }}", JOBS["engineering_closure_macos"])
        self.assertIn("JAE_EXPECTED_HEAD: ${{ needs.test.outputs.closure_head }}", JOBS["engineering_closure_macos"])

    def test_original_commands_fixtures_selectors_and_assertions_are_byte_preserved(self):
        # Frozen pre-closure step blocks: additions cannot silently weaken the
        # complete owning-file selectors, fixtures, assertions or original gates.
        expected = {
            "foundation": "37057629ec752cf5aed62c7e9c2604bd4b5ed9c6c8adf5b3b9bef987440f2d43",
            "packaged_candidate": "2b8dd98e7ae3c12deba1b2b07b0a58092d5effd80de58386057568452c078915",
            "macos_consumer_release": "ed3f87902b40be11d9eafa9d0b6446d9b8dec29c050b42045b305ae78f61bd06",
            "test": "a3bfe1e0fcbbe489db23a6c04b6d3b22e7981a610a444785968c73a0e5dce996",
        }
        for name, digest in expected.items():
            with self.subTest(job=name):
                body = JOBS[name]
                protected = body[body.index("    steps:\n"):]
                if name in {"packaged_candidate", "macos_consumer_release"}:
                    protected = body[body.index("    strategy:\n"):]
                if name == "macos_consumer_release":
                    self.assertEqual(protected.count(PREPARATION_MAC_STEP), 0)
                    protected = protected.replace(PREPARATION_MAC_STEP, "", 1)
                if name == "foundation":
                    self.assertEqual(protected.count(PREPARATION_STEP), 0)
                    protected = protected.replace(PREPARATION_STEP, "", 1)
                    self.assertEqual(protected.count(PUBLIC_PROBE_STEP),0)
                    self.assertEqual(protected.count(PUBLIC_PROBE_ARTIFACT),0)
                    protected = protected.replace(PUBLIC_PROBE_STEP,"",1).replace(PUBLIC_PROBE_ARTIFACT,"",1)
                    self.assertEqual(protected.count(STATIC_STEP), 1)
                    protected = protected.replace(STATIC_STEP, "", 1)
                    private_review_path = "            ${{ runner.temp }}/jae-preparation-ui/preparation-private-review.png\n"
                    self.assertEqual(protected.count(private_review_path), 0)
                    protected = protected.replace(private_review_path, "", 1)
                    resume_path = "            ${{ runner.temp }}/jae-preparation-ui/profile-editor-resume.png\n"
                    self.assertEqual(protected.count(resume_path), 1)
                    protected = protected.replace(resume_path, "", 1)
                if name == "test":
                    protected = body[body.index("      - uses: actions/setup-python@v5"):]
                protected = protected.rstrip() + "\n"
                self.assertEqual(hashlib.sha256(protected.encode()).hexdigest(), digest)
        self.assertIn("        suite: [consumer_transactions, recovery_transactions, release_distribution]\n", JOBS["packaged_candidate"])
        self.assertEqual(JOBS["macos_consumer_release"].count("          - suite:"), 4)
        self.assertEqual(JOBS["test"].count("run: python -m pytest -v\n"), 1)


class EngineeringClosureInlineGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jae-closure-guard-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin.git"
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Synthetic fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Synthetic fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git("init", "--bare", "--template=", str(self.origin))
        self.git("init", "--template=", str(self.checkout))
        (self.checkout / "tracked.txt").write_text("synthetic source\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "synthetic fixture")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "origin", "HEAD:refs/heads/draft/closure")
        self.payload = event(sha=self.sha)
        self.event_path = self.root / "event.json"
        self.output = self.root / "outputs.txt"
        self.repository_executed = self.root / "repository-executed"
        self.shadow_imported = self.root / "shadow-imported"
        self.env.update(GITHUB_EVENT_PATH=str(self.event_path), GITHUB_EVENT_NAME="pull_request",
                        GITHUB_REPOSITORY=REPOSITORY, GITHUB_OUTPUT=str(self.output),
                        JAE_EXPECTED_HEAD=self.sha, JAE_TEST_SENTINEL=str(self.repository_executed))
        # If -I is removed, json/re/subprocess shadowing aborts the guard and marks
        # unintended repository execution. No candidate import is ever needed.
        for module in ("json", "re", "subprocess", "sitecustomize"):
            (self.checkout / (module + ".py")).write_text(
                f"open({str(self.shadow_imported)!r}, 'w').write('IMPORTED')\n"
                "raise RuntimeError('repository code imported before admission')\n", encoding="utf-8",
            )

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.checkout, env=self.env,
                              check=True, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=15).stdout.strip()

    def run_guard(self, name):
        self.event_path.write_text(json.dumps(self.payload), encoding="utf-8")
        self.output.unlink(missing_ok=True)
        self.repository_executed.unlink(missing_ok=True)
        shell = guard_shell(name) + '\nprintf "EXECUTED" > "$JAE_TEST_SENTINEL"\n'
        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", shell],
                                cwd=self.checkout, env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertFalse(self.shadow_imported.exists(), result.stderr)
        return result

    def assert_refused(self):
        for name in CLOSURE:
            with self.subTest(job=name):
                result = self.run_guard(name)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(self.repository_executed.exists())
                self.assertFalse(self.output.exists())

    def test_actual_inline_guards_accept_exact_current_head_without_importing_repository(self):
        for name in CLOSURE:
            with self.subTest(job=name):
                result = self.run_guard(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.output.read_text(), "sha=" + self.sha + "\n")
                self.assertTrue(self.repository_executed.exists())
                self.assertIn("NOT_CERTIFIED", result.stdout)

    def test_remote_branch_advance_after_label_event_refuses_before_repository_execution(self):
        (self.checkout / "tracked.txt").write_text("new synthetic source\n", encoding="utf-8")
        self.git("commit", "-am", "branch moved after event")
        self.git("push", "origin", "HEAD:refs/heads/draft/closure")
        self.git("checkout", "--detach", self.sha)
        self.assertTrue(selected("test", self.payload))  # Event still looks eligible.
        self.assertTrue(selected("engineering_closure_macos", self.payload))
        self.assert_refused()

    def test_missing_remote_ref_fails_closed(self):
        self.git("push", "origin", "--delete", "draft/closure")
        self.assert_refused()

    def test_unavailable_remote_fails_closed(self):
        self.git("remote", "set-url", "origin", str(self.root / "absent.git"))
        self.assert_refused()

    def test_checkout_different_from_tested_sha_fails_closed(self):
        (self.checkout / "tracked.txt").write_text("different checkout\n", encoding="utf-8")
        self.git("commit", "-am", "wrong checkout")
        self.assert_refused()

    def test_python_guard_rejects_every_ineligible_label_and_wrong_test_output(self):
        original = copy.deepcopy(self.payload)
        cases = []
        for label in ("bug", "eng-" + OTHER_SHA, "eng-" + self.sha[:-1], "eng-" + self.sha + "0",
                      "eng-" + self.sha + "\n", "ENG-" + self.sha, "eng-" + self.sha.upper()):
            payload = copy.deepcopy(original)
            payload["label"]["name"] = label
            cases.append((payload, self.sha, "pull_request"))
        cases.append((event(sha=self.sha, fork=True), self.sha, "pull_request"))
        cases.append((event(sha=self.sha, action="synchronize"), self.sha, "pull_request"))
        cases.append((original, OTHER_SHA, "pull_request"))
        cases.append((original, self.sha, "push"))
        for payload, expected, event_name in cases:
            with self.subTest(label=payload["label"]["name"], expected=expected, event=event_name):
                self.payload = payload
                self.env.update(JAE_EXPECTED_HEAD=expected, GITHUB_EVENT_NAME=event_name)
                self.assert_refused()

    def test_case_insensitive_actions_comparison_cannot_admit_uppercase_label_execution(self):
        self.payload["label"]["name"] = "ENG-" + self.sha.upper()
        self.assertTrue(selected("test", self.payload))
        self.assert_refused()  # Python requires the exact lowercase 40-hex label.

    def test_invalid_sha_or_ref_is_refused_before_repository_execution(self):
        for sha in ("f" * 39, "f" * 41, "g" * 40, "F" * 40, "--help", "f" * 40 + "\n"):
            with self.subTest(sha=sha):
                self.payload = event(sha=sha)
                self.env["JAE_EXPECTED_HEAD"] = sha
                self.assert_refused()
        self.env["JAE_EXPECTED_HEAD"] = self.sha
        for ref in ("", "../main", "topic\nother", "topic*", "topic:other", "topic space"):
            with self.subTest(ref=ref):
                self.payload = event(sha=self.sha)
                self.payload["pull_request"]["head"]["ref"] = ref
                self.assert_refused()

    def test_valid_ref_with_shell_metacharacters_is_only_an_argv_value(self):
        ref = "draft/$(touch${IFS}PWNED)"
        self.git("push", "origin", "HEAD:refs/heads/" + ref)
        self.payload["pull_request"]["head"]["ref"] = ref
        for name in CLOSURE:
            with self.subTest(job=name):
                result = self.run_guard(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.checkout / "PWNED").exists())
                self.assertEqual(self.output.read_text(), "sha=" + self.sha + "\n")


if __name__ == "__main__":
    unittest.main()
