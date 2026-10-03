"""Build one unsigned engineering DMG; never publish or install it."""
from pathlib import Path
import argparse
import json
import sys


def main(argv=None):
    from executor.autonomy.macos_installer_image import build_installer_image
    parser = argparse.ArgumentParser()
    parser.add_argument('--distribution', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_installer_image(args.distribution, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
