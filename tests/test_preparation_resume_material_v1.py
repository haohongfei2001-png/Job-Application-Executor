"""Synthetic file authority; no native browser or recruiting-site requests."""
import hashlib
import json
from pathlib import Path
import pytest

from executor.preparation.authority import PreparationConflict
from executor.preparation.resume_material import retained_resume,MAX_RESUME_BYTES,RESUME_TYPES
from test_preparation_authority_v1 import fixture,offer,consume,SESSION


@pytest.fixture(params=['resume_pdf','resume_docx'])
def resume(fixture,request):
    q,task,profile,authority,now=fixture
    kind=request.param;name,_=RESUME_TYPES[kind]
    path=profile.parent/name
    data=(b'%PDF-SYNTHETIC\x00\xff' if kind=='resume_pdf' else b'PK\x03\x04SYNTHETIC_DOCX\x00\xff')+bytes(range(256))*3
    path.write_bytes(data)
    value=json.loads(profile.read_text());value['assets']={'resume':{'kind':kind,'path':str(path),'sha256':hashlib.sha256(data).hexdigest()}}
    profile.write_text(json.dumps(value));profile.chmod(0o600)
    permit=consume(fixture,offer(fixture))
    return fixture,permit,path,data,kind


def test_registered_pdf_and_docx_bytes_are_preserved_in_memory_without_path_projection(resume):
    (_,_,_,authority,_),permit,path,data,kind=resume
    with retained_resume(authority,permit,SESSION) as material:
        view=material.private_review();payload=material._file_payload()
        assert view['kind']==kind and view['byte_count']==len(data) and view['max_bytes']==MAX_RESUME_BYTES
        assert view['resume_sha256']==hashlib.sha256(data).hexdigest()
        assert payload=={'name':RESUME_TYPES[kind][0],'mimeType':RESUME_TYPES[kind][1],'buffer':data}
        assert str(path) not in json.dumps(view) and 'PRIVATE_AUTHORITY_CANARY' not in repr(material)
        assert material.__repr__()=='<ResumeMaterial>' and view['submit_capability'] is False
    with pytest.raises(PreparationConflict):material._file_payload()
    assert material._data is None


@pytest.mark.parametrize('change',['replace','write','symlink','session','profile','cancel'])
def test_retained_source_or_authority_drift_refuses_before_payload(resume,change):
    (q,task,profile,authority,_),permit,path,data,_=resume
    with pytest.raises((PreparationConflict,RuntimeError,OSError)):
        with retained_resume(authority,permit,SESSION) as material:
            if change=='replace':
                replacement=path.with_suffix('.replacement');replacement.write_bytes(data);replacement.replace(path)
            elif change=='write':path.write_bytes(b'x'*len(data))
            elif change=='symlink':
                outside=path.with_suffix('.outside');outside.write_bytes(data);path.unlink();path.symlink_to(outside)
            elif change=='session':authority.session_valid=lambda _:False
            elif change=='profile':profile.write_text('{"fields":{}}')
            else:q.cancel(task['task_id'])
            material._file_payload()
    assert material._data is None


def test_original_profile_digest_cannot_be_rebound_to_a_new_resume(resume):
    (_,_,profile,authority,_),permit,_,_,_=resume
    value=json.loads(profile.read_text());value['assets']['resume']['path']='/never/read/another.pdf'
    profile.write_text(json.dumps(value))
    with pytest.raises(PreparationConflict):
        with retained_resume(authority,permit,SESSION):pytest.fail('rebound material admitted')


def test_session_mismatch_refuses_before_file_read(resume,monkeypatch):
    (_,_,_,authority,_),permit,_,_,_=resume
    from executor.preparation import resume_material as module
    monkeypatch.setattr(module._LocalFile,'read',lambda _:pytest.fail('unauthorized source read'))
    with pytest.raises(PreparationConflict):
        with retained_resume(authority,permit,'x'*40):pass


def test_foreign_or_retired_permit_cannot_read_a_resume(resume,monkeypatch):
    (_,_,_,authority,_),permit,_,_,_=resume
    authority.active.clear()
    from executor.preparation import resume_material as module
    monkeypatch.setattr(module._LocalFile,'read',lambda _:pytest.fail('retired source read'))
    with pytest.raises(PreparationConflict):
        with retained_resume(authority,permit,SESSION):pass


def _material_case(fixture,kind,name,data,*,alias=None):
    q,task,profile,authority,_=fixture
    folder=profile.parent/'resume-assets';folder.mkdir()
    path=folder/name;path.write_bytes(data)
    if alias:
        original=folder/('original'+Path(name).suffix);path.rename(original)
        if alias=='symlink':path.symlink_to(original)
        else:
            import os
            os.link(original,path)
    value=json.loads(profile.read_text());value['assets']={'resume':{'kind':kind,'path':str(path),'sha256':hashlib.sha256(data).hexdigest()}}
    profile.write_text(json.dumps(value));profile.chmod(0o600)
    permit=consume(fixture,offer(fixture))
    return authority,permit,path


@pytest.mark.parametrize('size',[0,MAX_RESUME_BYTES,MAX_RESUME_BYTES+1])
def test_upload_size_ceiling_is_explicit_and_never_truncates(fixture,size):
    data=b'x'*size;authority,permit,_=_material_case(fixture,'resume_docx','resume.docx',data)
    if size==MAX_RESUME_BYTES:
        with retained_resume(authority,permit,SESSION) as material:
            assert material._file_payload()['buffer']==data
            assert material.private_review()['byte_count']==MAX_RESUME_BYTES
    else:
        with pytest.raises((PreparationConflict,RuntimeError)):
            with retained_resume(authority,permit,SESSION):pytest.fail('unsupported size admitted')


@pytest.mark.parametrize('kind,name,alias',[
    ('resume_doc','resume.doc',None),('resume_docx','resume.pdf',None),
    ('resume_pdf','resume.pdf','symlink'),('resume_pdf','resume.pdf','hardlink')])
def test_unsupported_type_extension_or_existing_alias_never_returns_payload(fixture,kind,name,alias):
    authority,permit,_=_material_case(fixture,kind,name,b'SYNTHETIC_ONLY',alias=alias)
    with pytest.raises((PreparationConflict,RuntimeError)):
        with retained_resume(authority,permit,SESSION):pytest.fail('unsupported source admitted')


def test_parent_substitution_is_fenced_without_reading_outside_canary(fixture):
    authority,permit,path=_material_case(fixture,'resume_pdf','resume.pdf',b'APPROVED_SYNTHETIC')
    outside=path.parent.parent/'outside';outside.mkdir();canary=outside/'resume.pdf';canary.write_bytes(b'OUTSIDE_CANARY')
    with pytest.raises((PreparationConflict,RuntimeError)):
        with retained_resume(authority,permit,SESSION) as material:
            retained=path.parent.with_name('retained-assets');path.parent.rename(retained);path.parent.symlink_to(outside)
            material._file_payload()
    assert canary.read_bytes()==b'OUTSIDE_CANARY'
