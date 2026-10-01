"""Private, retained source bytes for a separately approved resume upload.

No browser/network/approval capability. File bytes are never transformed or
persisted here; repr is redacted. A future owner must consume upload authority
before passing the in-memory FilePayload to a native control. This deliberately
does not reuse a disk path after approval, because opaque CDP transport drops
whole disk-backed multipart file bytes on the tested browser versions.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

from ..autonomy.task_preparation import _LocalFile, _Unavailable
from .authority import PreparationConflict, _material, _sha
from .qiyunfang import CONTRACT_URL

# Upload ceiling only: existing profile preparation may hash up to its 20 MiB
# supported local-source budget before this narrower upload admission.
MAX_RESUME_BYTES = 4 * 1024 * 1024
RESUME_TYPES = {
    'resume_pdf': ('resume.pdf', 'application/pdf'),
    'resume_docx': ('resume.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
}


class ResumeMaterial:
    def __init__(self, kind, version, data, fence, task_fence):
        self._kind, self._version, self._data, self._fence = kind, version, data, fence
        self._task_fence=task_fence
        self._retired = False

    def __repr__(self): return '<ResumeMaterial>'

    def _fence_files(self):
        """No SQLite/browser RPC; paired with the journal's in-transaction task check."""
        if (self._retired or self._data is None or self._kind not in RESUME_TYPES
                or hashlib.sha256(self._data).hexdigest()!=self._version):raise PreparationConflict()
        self._fence()

    def fence(self):
        self._fence_files();self._task_fence()

    def _metadata(self):
        name,mime=RESUME_TYPES[self._kind]
        return {'resume_sha256':self._version,'byte_count':len(self._data),'kind':self._kind,
                'destination_filename':name,'mime_type':mime,'recipient_url':CONTRACT_URL,
                'max_bytes':MAX_RESUME_BYTES,'submit_capability':False}

    def private_review(self):
        self.fence();return self._metadata()

    def _file_payload(self):
        """Owner-only ephemeral bytes, never an HTTP/diagnostic/model projection."""
        self.fence()
        name,mime=RESUME_TYPES[self._kind]
        return {'name':name,'mimeType':mime,'buffer':self._data}

    def retire(self):
        # Drop this reference; immutable Python bytes are not secure-zeroized.
        self._retired=True;self._data=None


@contextmanager
def retained_resume(authority,permit,session):
    """Bind the original prepared task/profile/registered resume through effect.

    This helper grants no upload permission. The caller separately checks the
    prepared task/unique upload slot and must retain this context until the last
    guarded operation returns. Missing, unregistered, changed or unsupported
    files never fall back to another resume, conversion or an empty payload.
    """
    def private():
        authority._session(session)
        if (not isinstance(permit,dict) or authority.active.get(permit.get('nonce_sha'))!=permit
                or permit.get('authority_sha')!=authority.instance_sha or permit.get('session_sha')!=_sha(session)):
            raise PreparationConflict()
    private()
    with _material(authority.queue,permit['task_id'],permit['task_revision']) as (_,report,profile,source_fence):
        if (report['profile']['version']!=permit['profile_sha'] or report['resume']!=permit['resume']
                or report['resume']['status']!='local_version_matches'):
            raise PreparationConflict()
        asset=profile.get('assets',{}).get('resume',{})
        kind=asset.get('kind')
        if kind not in RESUME_TYPES:raise PreparationConflict()
        with _LocalFile(asset.get('path'),MAX_RESUME_BYTES,private=False) as local:
            try:data=local.read()
            except _Unavailable:raise PreparationConflict() from None
            if not data or hashlib.sha256(data).hexdigest()!=report['resume']['version']:
                raise PreparationConflict()
            def fence():
                private();source_fence();local.fence()
            def task_fence():
                if authority.queue.get(permit['task_id'])['revision']!=permit['task_revision']:
                    raise PreparationConflict()
            value=ResumeMaterial(kind,report['resume']['version'],data,fence,task_fence)
            try:
                value.fence();yield value;value.fence()
            finally:value.retire()
