"""Check the actual Git index and copy-safe fixture package, not source promises."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tracked_artifacts_exclude_private_runtime_and_unreviewed_media():
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT,
    ).decode().split("\0")
    tracked = [path for path in tracked if path]
    private_roots = ("runtime/", "logs/", "runs/", "screenshots/", "chrome-profile/")
    assert not any(path.startswith(private_roots) for path in tracked)
    assert not any(path.startswith("applications/") and path != "applications/.gitkeep" for path in tracked)
    assert not any(path.startswith("config/") and not path.endswith(".example.json") for path in tracked)
    assert not any(path.lower().endswith((".docx", ".pdf", ".png", ".jpg", ".jpeg", ".har", ".zip", ".trace")) for path in tracked)


def test_syntheticats_manifest_is_copy_safe_and_bounded():
    path = ROOT / "tests/fixtures/syntheticats-v1.manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "origin_class", "capture_date", "contract_version", "capture_method",
        "privacy_class", "transformations", "valid_interactions", "limitations",
        "golden_expected_outcome", "synthetic_replacements", "network_allowlist",
    }
    assert required <= set(manifest)
    assert manifest["origin_class"] == "SYNTHETIC"
    assert manifest["privacy_class"] == "COPY_SAFE_SYNTHETIC"
    assert manifest["network_allowlist"] == ["127.0.0.1"]
    assert manifest["golden_expected_outcome"]["submit_count"] == 0
