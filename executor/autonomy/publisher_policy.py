"""External publisher requirements for an already trusted verifier.

No candidate metadata, environment variable, writable preference or signer
display string can configure this policy. The shipped default admits no signed
consumer payload. Configuring a real publisher is a separately reviewed build
change; a candidate's own copy is not a first-install trust root.
"""
from __future__ import annotations

import re
import subprocess
import sys

BUNDLE_ID = "com.local.job-application-executor.ai-application-manager"
BUILT_IN_PUBLISHER_POLICY = None


def resolve_policy(required_publisher_policy=None):
    value = (BUILT_IN_PUBLISHER_POLICY if required_publisher_policy is None
             else required_publisher_policy)
    if (type(value) is not dict or set(value) != {
            'format', 'team_id', 'bundle_id', 'certificate_kind'}
            or value.get('format') != 'jae-required-publisher-policy-v1'
            or type(value.get('team_id')) is not str
            or re.fullmatch(r'[A-Z0-9]{10}', value['team_id']) is None
            or value.get('bundle_id') != BUNDLE_ID
            or value.get('certificate_kind') != 'Developer ID Application'):
        raise ValueError('signed_payload_publisher_policy_required')
    return dict(value)


def verify_publisher(app, required_publisher_policy=None):
    """Use Apple's verifier before reading a signed payload's manifest.

This establishes static publisher/integrity evidence only, not notarization,
Gatekeeper acceptance, a selected release, installation or runtime health.
"""
    policy = resolve_policy(required_publisher_policy)
    if sys.platform != 'darwin':
        raise ValueError('signed_payload_requires_macos')
    requirement = ('anchor apple generic and '
        'certificate 1[field.1.2.840.113635.100.6.2.6] exists and '
        'certificate leaf[field.1.2.840.113635.100.6.1.13] exists and '
        f'certificate leaf[subject.OU] = "{policy["team_id"]}" and '
        f'identifier = "{policy["bundle_id"]}"')
    try:
        result = subprocess.run([
            '/usr/bin/codesign', '--verify', '--strict', '--all-architectures',
            '--test-requirement', '=' + requirement, str(app),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'},
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('signed_payload_publisher_unavailable') from exc
    if result.returncode != 0:
        raise ValueError('signed_payload_publisher_refused')
    return policy
