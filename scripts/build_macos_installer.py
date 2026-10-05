"""Build one engineering DMG; never publish, install or configure runtime trust."""
from pathlib import Path
import argparse
import json
import sys


def main(argv=None):
    from executor.autonomy.macos_installer_image import build_installer_image
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--distribution', type=Path)
    source.add_argument('--signed-app', type=Path)
    parser.add_argument('--team-id')
    parser.add_argument('--bundle-id')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.signed_app is None:
        if args.team_id is not None or args.bundle_id is not None:
            parser.error('publisher parameters require --signed-app')
        result = build_installer_image(args.distribution, args.output)
    else:
        if args.team_id is None or args.bundle_id is None:
            parser.error('--signed-app requires --team-id and --bundle-id from the trusted build operator')
        from executor.autonomy.publisher_policy import resolve_policy
        policy = resolve_policy({'format': 'jae-required-publisher-policy-v1',
            'team_id': args.team_id, 'bundle_id': args.bundle_id,
            'certificate_kind': 'Developer ID Application'})
        result = build_installer_image(output=args.output, signed_app=args.signed_app,
                                       required_publisher_policy=policy)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
