"""Cloud/operator build entrypoint; no deployment, signing or owner installation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    from executor.autonomy.app_distribution import build_macos_distribution

    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = build_macos_distribution(
        source, standalone_runtime=args.standalone_runtime, output_dir=args.output)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
