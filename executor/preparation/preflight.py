"""Read-only public-page bootstrap for the dedicated disposable session."""
from __future__ import annotations

import time
from dataclasses import dataclass,field

from .qiyunfang import CONTRACT_URL,ROOT,OBSERVE_ROOT,validate_observation,ContractChanged


@dataclass(frozen=True,repr=False)
class PublicFormPreflight:
    page: object = field(repr=False)
    document: object = field(repr=False)
    resources_sha: str


def open_public_form(owner):
    """Open only the observed public application link; never load a profile."""
    if (owner.identity is None or owner.context.pages or owner.transport.phase!='READ_ONLY'):
        raise ContractChanged()
    process_sha=owner.identity.verify(owner.browser)
    page=owner.context.new_page()
    page.goto(CONTRACT_URL,wait_until='domcontentloaded',timeout=30000)
    button=page.locator('#module2398FlBtn')
    if button.count()!=1 or button.get_attribute('href')!='javascript: JZ.transformForLinkFunc("linkForm", 6);':
        raise ContractChanged()
    button.click(timeout=10000)
    page.locator(ROOT).wait_for(state='visible',timeout=15000)
    deadline=time.monotonic()+10
    while True:
        try:validate_observation(page.locator(ROOT).evaluate(OBSERVE_ROOT));break
        except ContractChanged:
            if time.monotonic()>=deadline:raise
            page.wait_for_timeout(100)
    receipt=owner.transport.certify_discovery()
    document=owner.observe(page,resources_sha=receipt['resources_sha'])
    validate_observation(page.locator(ROOT).evaluate(OBSERVE_ROOT))
    if owner.identity.verify(owner.browser)!=process_sha:
        raise ContractChanged()
    return PublicFormPreflight(page,document,receipt['resources_sha'])


def prepare_public_flow(authority,owner,*,task_id,revision,session,selected_ids,still_authorized=lambda:True):
    """Internal opening/review stage only. Explicit approval is a later action."""
    from .flow import PreparationFlow
    preflight=open_public_form(owner)
    if still_authorized() is not True:raise ContractChanged()
    flow=PreparationFlow(authority,owner,preflight.page,task_id=task_id,revision=revision,
        session=session,selected_ids=selected_ids,resources_sha=preflight.resources_sha,still_authorized=still_authorized)
    if flow.binding!=preflight.document.public_binding():
        flow.close();raise ContractChanged()
    return flow
