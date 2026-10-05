"""Cloud/operator build entrypoint; no deployment, signing or owner installation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    from executor.autonomy.app_distribution import build_macos_distribution

    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    presentation = parser.add_mutually_exclusive_group()
    presentation.add_argument("--native-presentation", dest="native_presentation",
                              action="store_true")
    presentation.add_argument("--web-fallback", dest="native_presentation",
                              action="store_false")
    parser.set_defaults(native_presentation=True)
    parser.add_argument("--signing-workspace", type=Path)
    parser.add_argument("--publisher-team-id")
    parser.add_argument("--publisher-bundle-id")
    args = parser.parse_args(argv)
    policy = None
    if args.signing_workspace is not None:
        from scripts.prepare_macos_signing import publisher_policy
        policy = publisher_policy(args.publisher_team_id, args.publisher_bundle_id)
        if not args.native_presentation:
            parser.error("signing preparation requires the native distribution")
    elif args.publisher_team_id is not None or args.publisher_bundle_id is not None:
        parser.error("publisher policy requires --signing-workspace")
    # Normal delivered artifacts use the verified native shell. An explicit
    # compatibility fallback remains available; neither mode is certification.
    options = {"standalone_runtime": args.standalone_runtime, "output_dir": args.output,
               "native_presentation": args.native_presentation}
    receipt = build_macos_distribution(Path(__file__).resolve().parents[1], **options)
    if args.signing_workspace is not None:
        from executor.autonomy.app_distribution import RECEIPT_NAME, _read_distribution_receipt
        from scripts.prepare_macos_signing import prepare_signing_workspace, _encoded, IDENTITY
        import hashlib
        # Pin the exact receipt authored by this invocation, not a candidate's
        # self-declared signer. Preparation never activates signed admission.
        actual, encoded = _read_distribution_receipt(args.output.expanduser().absolute() / RECEIPT_NAME)
        if actual != receipt:
            raise ValueError("signing_build_receipt_changed")
        prepared = prepare_signing_workspace(args.output, args.signing_workspace,
            expected_receipt_sha256=hashlib.sha256(encoded).hexdigest(),
            required_publisher_policy=policy)
        identity_bytes = _encoded(prepared)
        if (args.signing_workspace.expanduser().absolute() / IDENTITY).read_bytes() != identity_bytes:
            raise ValueError("signing_build_preparation_changed")
        receipt = {**receipt, "signing_preparation": {
            **{key: prepared[key] for key in ("phase", "bundle_sha256", "consumer_admission")},
            "finalization_inputs": {
                "entry": "scripts/finalize_macos_signing.py",
                "expected_receipt_sha256": hashlib.sha256(encoded).hexdigest(),
                "expected_prepared_identity_sha256": hashlib.sha256(identity_bytes).hexdigest(),
                "expected_policy_sha256": hashlib.sha256(_encoded(policy)).hexdigest(),
            },
            "current_payload_stages": {
                "entry": "scripts/finalize_macos_signing.py",
                "inputs": "same trusted finalization_inputs and publisher policy",
                "after_nested_signing": "--current-payload-stage prepare",
                "after_outer_signing": "--current-payload-stage finalize",
                "output_contract": "jae-build-bundle-identity-v3",
                "consumer_admission": "NOT_ADMITTED",
            }}}
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
