"""Value-free identity observation for a caller-owned fresh browser context.

The caller owns and creates the context. This module does not discover or adopt
an existing tab, assert account identity, or create website execution authority.
"""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field

from .qiyunfang import CONTRACT_URL, ROOT, OBSERVE_SHAPE_ROOT, ContractChanged, digest


@dataclass(frozen=True, repr=False)
class PreparationDocument:
    binding: dict = field(repr=False)
    context_id: str = field(repr=False)
    target_id: str = field(repr=False)

    def public_binding(self):
        return copy.deepcopy(self.binding)


def observe_owned_document(page, *, process_sha, resources_sha):
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
           for value in (process_sha, resources_sha)):
        raise ContractChanged()
    if (page.is_closed() or page.url != CONTRACT_URL or len(page.frames) != 1
            or len(page.context.pages) != 1 or page.locator(ROOT).count() != 1):
        raise ContractChanged()
    cdp = page.context.new_cdp_session(page)
    try:
        target = cdp.send('Target.getTargetInfo')['targetInfo']
        context_id, target_id = target.get('browserContextId'), target.get('targetId')
        if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value)
               for value in (context_id, target_id)):
            raise ContractChanged()
        document = cdp.send('DOM.getDocument', {'depth': 0})['root']
        node = cdp.send('DOM.querySelector', {'nodeId': document['nodeId'], 'selector': ROOT})['nodeId']
        root_id = cdp.send('DOM.describeNode', {'nodeId': node, 'depth': 0})['node']['backendNodeId']
        if type(root_id) is not int or root_id <= 0:
            raise ContractChanged()
    finally:
        cdp.detach()
    observed = page.locator(ROOT).evaluate(OBSERVE_SHAPE_ROOT)
    # Opaque digests only, never applicant values or protected inputs.
    binding = {'process_sha': process_sha,
        'context_sha': hashlib.sha256(context_id.encode()).hexdigest(),
        'document_sha': digest([CONTRACT_URL, target_id, page.evaluate('performance.timeOrigin')]),
        'root_sha': digest([target_id, root_id]), 'controls_sha': digest(observed),
        'resources_sha': resources_sha}
    return PreparationDocument(binding, context_id, target_id)


def observe_context_absence(browser, expected, *, process_sha):
    """Prove exact-context absence only on the same independently owned process.

    A missing/unreachable/replaced browser is UNKNOWN, not absence evidence.
    The process owner supplies its freshly reverified process identity. This
    function sends only read-only CDP inventory commands and does not close tabs.
    """
    if expected.get('process_sha') != process_sha:
        return {'status':'UNKNOWN'}
    cdp = None
    try:
        cdp = browser.new_browser_cdp_session()
        contexts = cdp.send('Target.getBrowserContexts').get('browserContextIds')
        if not isinstance(contexts, list) or any(not isinstance(item, str) for item in contexts):
            return {'status':'UNKNOWN'}
        if any(hashlib.sha256(item.encode()).hexdigest() == expected.get('context_sha') for item in contexts):
            return {'status':'PRESENT'}
        return {'status':'ABSENT', 'browser':copy.deepcopy(expected)}
    except Exception:
        return {'status':'UNKNOWN'}
    finally:
        if cdp is not None:
            try: cdp.detach()
            except Exception: pass
