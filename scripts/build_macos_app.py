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
    args = parser.parse_args(argv)
    # Normal delivered artifacts use the verified native shell. An explicit
    # compatibility fallback remains available; neither mode is certification.
    options = {"standalone_runtime": args.standalone_runtime, "output_dir": args.output,
               "native_presentation": args.native_presentation}
    receipt = build_macos_distribution(Path(__file__).resolve().parents[1], **options)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
