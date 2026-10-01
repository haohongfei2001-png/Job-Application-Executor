"""One-pass form primitive kernel, separate from the account/draft executor.

Not registered for production: lifecycle, durable approval and human handoff are
mandatory at the session boundary. Tests use synthetic loopback form replicas.
"""
from __future__ import annotations

import copy

from .qiyunfang import (CONTRACT_URL, ROOT, OBSERVE_ROOT, ROUTINE_FIELDS,
                       ContractChanged, validate_observation, _text)


class PreparationKernel:
    def __init__(self, page, transport, guard, journal):
        self.page, self.transport = page, transport
        self.guard, self.journal = guard, journal
        self.root = None
        self.expected = {}
        self.started = False
        self.document_epoch = None
        self.actions = []

    def _fence(self):
        self.guard()
        self.transport.require_sealed()
        if (self.page.is_closed() or self.page.url != CONTRACT_URL
                or len(self.page.context.pages) != 1 or len(self.page.frames) != 1
                or self.page.evaluate("performance.timeOrigin") != self.document_epoch
                or self.page.locator(ROOT).count() != 1
                or not self.root.evaluate("(root, selector) => root === document.querySelector(selector)", ROOT)):
            raise ContractChanged()
        validate_observation(self.root.evaluate(OBSERVE_ROOT), expected_values=self.expected)

    def run(self, plan):
        if self.started:
            raise RuntimeError("preparation kernel is one-shot")
        self.started = True
        if not callable(getattr(self.journal, "invalidate", None)):
            raise ValueError("preparation journal lacks retained-proof invalidation")
        # Caller gives only a plan built from the private exact-profile review.
        allowed = {field.field_id: field for field in ROUTINE_FIELDS}
        if (not isinstance(plan, list) or not plan
                or any(not isinstance(item, dict) or set(item) != {"field_id", "value"}
                       or not isinstance(item["field_id"], str)
                       or item["field_id"] not in allowed for item in plan)
                or len({item["field_id"] for item in plan}) != len(plan)):
            raise ValueError("invalid preparation plan")
        for item in plan:
            field = allowed[item["field_id"]]
            value = item["value"]
            if field.control == "text":
                if not _text(value, field.maxlength):
                    raise ValueError("invalid preparation plan")
            elif field.control == "radio":
                if (not isinstance(value, str) or value not in field.options
                        or (field.field_id == "8" and value != field.options[19])):
                    raise ValueError("invalid preparation plan")
            elif (not isinstance(value, list) or not value or any(not isinstance(v, str) or v not in field.options for v in value)
                  or len(value) != len(set(value))):
                raise ValueError("invalid preparation plan")
        plan = copy.deepcopy(plan)
        if self.page.locator(ROOT).count() != 1:
            raise ContractChanged()
        self.root = self.page.locator(ROOT).element_handle()
        self.document_epoch = self.page.evaluate("performance.timeOrigin")
        try:
            self._fence()
            for item in plan:
                field, value = allowed[item["field_id"]], item["value"]
                primitives = value if field.control == "checkbox" else [value]
                for primitive in primitives:
                    self._fence()
                    # Each primitive gets its own value-free durable intent.
                    action = self.journal.before(field.field_id)
                    self.actions.append(action)
                    try:
                        self._fence()
                        row = self.page.locator(field.selector)
                        if field.control == "text":
                            row.locator('input[type="text"]').fill(primitive)
                            self.expected[field.field_id] = primitive
                        else:
                            row.locator(f'#M1567R{field.field_id}I{field.options.index(primitive)}').check()
                            if field.control == "checkbox":
                                self.expected.setdefault(field.field_id, []).append(primitive)
                            else:
                                self.expected[field.field_id] = primitive
                        self._fence()
                        self.journal.after(action, "READBACK_VERIFIED")
                    except BaseException:
                        self.journal.after(action, "UNKNOWN_OUTCOME")
                        raise
            self._fence()
        except BaseException:
            if self.actions:
                self.journal.invalidate()
            raise
        return {"status": "PREPARED_UNVERIFIED", "field_count": len(plan),
                "account_verified": False, "server_draft_verified": False,
                "ready_to_submit": False, "submit_capability": False}
