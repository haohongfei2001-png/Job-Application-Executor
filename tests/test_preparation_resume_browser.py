"""Synthetic owned preparation + synchronous lookup XHR + exact resume sender.

No requests or files reach a real employer. A test-only client maps the fixed
approved request paths to an independent loopback recipient with its own cookie.
This proves the owner/callback protocol, not the live site's upload semantics.
"""
import hashlib
import json
import sys
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlsplit

import pytest

from executor.preparation.authority import PreparationConflict
from executor.preparation.flow import PreparationFlow
from executor.preparation.resume_flow import ResumeUploadFlow
from executor.preparation.resume_material import RESUME_TYPES
from executor.preparation.resume_transport import LOOKUP_URL,UPLOAD_URL,UPLOAD_PATTERN,SPLIT_BYTES
from executor.preparation.session import DisposablePreparationSession
from executor.preparation.qiyunfang import CONTRACT_URL,ROOT,digest
from test_preparation_authority_browser import setup,SESSION
from test_qiyunfang_preparation_browser import replica,bounded_browser_oracle


@pytest.mark.parametrize('kind',['resume_pdf','resume_docx'])
@pytest.mark.parametrize('fault',['none','nonzero_size','redirect','root_before_approve','root_during_selection'])
def test_owned_resume_stage_is_exact_private_nonreplayable_and_synchronous_xhr_safe(tmp_path,kind,fault):
    q,task,profile,authority=setup(tmp_path)
    filename,mime=RESUME_TYPES[kind]
    original=(b'%PDF-SYNTHETIC' if kind=='resume_pdf' else b'PK\x03\x04SYNTHETIC_DOCX')+b'\x00\xff'+bytes(range(256))*3
    asset=tmp_path/filename;asset.write_bytes(original)
    value=json.loads(profile.read_text());value['assets']={'resume':{'path':str(asset),'kind':kind,'sha256':hashlib.sha256(original).hexdigest()}}
    profile.write_text(json.dumps(value));profile.chmod(0o600)
    received=[];calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            received.append(('GET',self.path,None,{}))
            self.send_response(200);self.send_header('Set-Cookie','upload_fixture=ANONYMOUS_CANARY; HttpOnly; Path=/')
            self.end_headers();self.wfile.write(b'<!doctype html><p>Fixture session established</p>')
        def do_POST(self):
            data=self.rfile.read(int(self.headers.get('Content-Length','0')))
            content_type=self.headers.get('Content-Type','');headers=dict(self.headers)
            if content_type.startswith('multipart/form-data;'):
                message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\n\r\n').encode()+data)
                parts={}
                for part in message.iter_parts():
                    name=part.get_param('name',header='content-disposition')
                    assert name not in parts
                    parts[name]=(part.get_filename(),part.get_content_type(),part.get_payload(decode=True))
                body=parts
            else:body=parse_qs(data.decode(),strict_parsing=True)
            received.append(('POST',self.path,body,headers))
            if fault=='redirect':
                self.send_response(307);self.send_header('Location','/never-follow');self.end_headers();return
            response={'success':True,'size':1 if fault=='nonzero_size' else 0,'id':'SYNTHETIC_ATTACHMENT'}
            raw=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    script='''
    document.querySelector('[data-formid="11"] input[type="file"]').addEventListener('change',function(){
      const file=this.files[0];
      try {
        if(FAULT==='root_during_selection'){
          const root=document.querySelector('.form_container');root.outerHTML=root.outerHTML;
        }
        for(let n=0;n<2;n++){
          const xhr=new XMLHttpRequest();xhr.open('POST',LOOKUP,false);
          xhr.setRequestHeader('Content-Type','application/x-www-form-urlencoded; charset=UTF-8');
          xhr.send('fileMd5=UNTRUSTED_NATIVE_NONCE&identity=SYNTHETIC_PROTECTED_CANARY');
          if(xhr.status!==200)return;
        }
        const form=new FormData();form.append('filedata',file,file.name);
        form.append('identity','SYNTHETIC_PROTECTED_CANARY');form.append('unapproved','NEVER_FORWARD');
        const xhr=new XMLHttpRequest();xhr.open('POST',TARGET,true);xhr.send(form);
      } catch(_) { document.body.dataset.syntheticUploadRefused='yes'; }
    });
    '''.replace('FAULT',json.dumps(fault)).replace('LOOKUP',json.dumps(LOOKUP_URL)).replace('TARGET',json.dumps(UPLOAD_URL+'?cmd=_mobiupload&app=21&type=0&fileUploadLimit=1&pieceUpload=true&checkId=6&checkItemId=11&bizType=5'))
    try:
        with DisposablePreparationSession(headless=True,channel='chrome' if sys.platform=='linux' else None) as owner:
            owner.context.route(CONTRACT_URL,lambda route:route.fulfill(status=200,content_type='text/html',body=replica(script)))
            page=owner.context.new_page();page.goto(CONTRACT_URL)
            flow=PreparationFlow(authority,owner,page,task_id=task['task_id'],revision=task['revision'],session=SESSION,
                                 selected_ids=['0','5','8'],resources_sha=digest('synthetic upload fixture'))
            offered=flow.private_offer();flow.approve(offered['nonce'],offered['scope_sha'],SESSION,approve_transmission=True)
            actual_client=owner.client
            response=actual_client.get(base+'/seed',max_redirects=0,max_retries=0);response.dispose()
            class LocalRecipient:
                def post(self,url,**options):
                    assert url==LOOKUP_URL or UPLOAD_PATTERN.fullmatch(url)
                    calls.append(url);parsed=urlsplit(url)
                    return actual_client.post(base+parsed.path+'?'+parsed.query,**options)
                def dispose(self):actual_client.dispose()
            # Only this synthetic fixture remaps recipients. Product sender has
            # no configurable destination and only the canonical fixed origin.
            owner.client=LocalRecipient()
            upload=None
            try:
                upload=ResumeUploadFlow(flow);offer=upload.private_offer()
                if fault=='root_before_approve':page.evaluate("const root=document.querySelector('.form_container');root.outerHTML=root.outerHTML")
                if fault=='none':
                    result=upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
                    assert result['status']=='RETURNED_UNVERIFIED' and result['server_attachment_verified'] is False
                else:
                    with pytest.raises((PreparationConflict,RuntimeError)):
                        upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
                with pytest.raises(PreparationConflict):upload.approve(offer['nonce'],offer['scope_sha'],approve_upload=True)
            finally:
                if owner.pw is not None:owner.client=actual_client
                if upload is not None:upload._retire()
                if not flow.closure_attempted:flow.close()
            posts=[row for row in received if row[0]=='POST']
            if fault=='root_before_approve':assert posts==[]
            elif fault in {'nonzero_size','redirect'}:assert len(posts)==1
            else:
                assert len(posts)==3
                first,second,final=posts
                assert first[2]==second[2]
                assert set(first[2])=={'fileMd5','fileSplitSize','fileName','totalSize'}
                assert first[2]['fileName']==[filename] and first[2]['totalSize']==[str(len(original))]
                assert first[2]['fileSplitSize']==[str(SPLIT_BYTES)]
                nonce=first[2]['fileMd5'][0];assert len(nonce)==32 and nonce!='UNTRUSTED_NATIVE_NONCE'
                assert set(final[2])=={'filedata','fileMd5','totalSize','complete','initSize'}
                assert final[2]['filedata']==(filename,mime,original)
                assert final[2]['fileMd5'][2]==nonce.encode() and final[2]['complete'][2]==b'true' and final[2]['initSize'][2]==b'0'
            assert received[0][0:2]==('GET','/seed') and all(row[1]!='/never-follow' for row in received)
            for row in posts:
                headers={key.lower():value for key,value in row[3].items()}
                assert headers.get('cookie')=='upload_fixture=ANONYMOUS_CANARY'
                assert headers.get('x-requested-with')=='XMLHttpRequest' and 'authorization' not in headers
                assert 'SYNTHETIC_PROTECTED_CANARY' not in repr(row[2]) and 'NEVER_FORWARD' not in repr(row[2])
            with q.tx() as db:
                slots=[dict(row) for row in db.execute('SELECT * FROM preparation_resume_uploads')]
                stages=[dict(row) for row in db.execute('SELECT * FROM preparation_resume_stages')]
            if fault=='root_before_approve':assert slots==stages==[]
            else:
                assert len(slots)==1 and slots[0]['outcome']==('RETURNED_UNVERIFIED' if fault=='none' else 'UNKNOWN_OUTCOME')
                assert 'SYNTHETIC_PROTECTED_CANARY' not in json.dumps(slots+stages)
            assert not q.preparation_in_flight()
    finally:server.shutdown();server.server_close();thread.join()


def test_private_controller_preserves_two_mib_docx_and_one_upload_authority(tmp_path):
    """Synthetic internal commands, real owner thread and independent recipient."""
    from executor.preparation.controller import PreparationController
    q,task,profile,_=setup(tmp_path)
    original=b'PK\x03\x04SYNTHETIC_DOCX'+bytes(range(256))*8192
    asset=tmp_path/'original.docx';asset.write_bytes(original)
    value=json.loads(profile.read_text());value['assets']={'resume':{'path':str(asset),'kind':'resume_docx',
        'sha256':hashlib.sha256(original).hexdigest()}}
    profile.write_text(json.dumps(value));profile.chmod(0o600)
    received=[];owner_threads=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Set-Cookie','controller_upload=ANONYMOUS; HttpOnly; Path=/')
            self.end_headers();self.wfile.write(b'fixture')
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length']))
            if self.headers.get('Content-Type','').startswith('multipart/form-data;'):
                message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+raw)
                parts=list(message.iter_parts())
                assert len(parts)==5
                file=next(part for part in parts if part.get_param('name',header='content-disposition')=='filedata')
                received.append(('file',file.get_filename(),file.get_content_type(),file.get_payload(decode=True)))
            else:received.append(('lookup',parse_qs(raw.decode(),strict_parsing=True)))
            assert self.headers.get('Cookie')=='controller_upload=ANONYMOUS'
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(b'{"success":true,"size":0,"id":"SYNTHETIC"}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    base=f'http://127.0.0.1:{server.server_port}'
    script='''document.querySelector('[data-formid="11"] input[type="file"]').addEventListener('change',function(){
      const file=this.files[0];
      for(let n=0;n<2;n++){const x=new XMLHttpRequest();x.open('POST',LOOKUP,false);
        x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');x.send('untrusted=DISCARD');if(x.status!==200)return;}
      const form=new FormData();form.append('filedata',file,file.name);form.append('identity','DISCARD_PROTECTED');
      const x=new XMLHttpRequest();x.open('POST',TARGET,true);x.send(form);
    });'''.replace('LOOKUP',json.dumps(LOOKUP_URL)).replace('TARGET',json.dumps(UPLOAD_URL+'?cmd=_mobiupload&app=21&type=0&fileUploadLimit=4&pieceUpload=true&checkId=6&checkItemId=11&bizType=5'))
    def flow_factory(authority,owner,**kwargs):
        owner_threads.append(threading.get_ident())
        owner.context.route(CONTRACT_URL,lambda route:route.fulfill(status=200,content_type='text/html',body=replica(script)))
        page=owner.context.new_page();page.goto(CONTRACT_URL)
        actual=owner.client;seed=actual.get(base+'/seed',max_redirects=0,max_retries=0);seed.dispose()
        class LocalRecipient:
            def post(self,url,**options):
                owner_threads.append(threading.get_ident())
                assert url==LOOKUP_URL or UPLOAD_PATTERN.fullmatch(url)
                parsed=urlsplit(url);return actual.post(base+parsed.path+'?'+parsed.query,**options)
            def dispose(self):actual.dispose()
        owner.client=LocalRecipient()
        return PreparationFlow(authority,owner,page,resources_sha=digest('synthetic controller upload'),**kwargs)
    controller=PreparationController(q,lambda session:session==SESSION,flow_factory=flow_factory,
        owner_factory=lambda:DisposablePreparationSession(headless=True,channel='chrome' if sys.platform=='linux' else None),
        write_admission=lambda _:True,upload_admission=lambda _:True)
    try:
        offer=controller.open(task['task_id'],task['revision'],SESSION,['0','5','8'])
        assert controller.approve(offer['nonce'],offer['scope_sha'],SESSION,approve_transmission=True)['field_count']==3
        upload=controller.review_resume(SESSION)
        assert upload['resume']['kind']=='resume_docx' and upload['resume']['byte_count']==len(original)
        assert upload['resume']['resume_sha256']==hashlib.sha256(original).hexdigest()
        result=controller.approve_resume(upload['nonce'],upload['scope_sha'],SESSION,approve_upload=True)
        assert result['status']=='RETURNED_UNVERIFIED' and not result['server_attachment_verified']
        assert controller.status()['resume_status']=='RETURNED_UNVERIFIED'
        with pytest.raises(PreparationConflict):controller.approve_resume(upload['nonce'],upload['scope_sha'],SESSION,approve_upload=True)
        with pytest.raises(PreparationConflict):controller.review_resume(SESSION)
        assert [row[0] for row in received]==['lookup','lookup','file']
        assert received[-1]==('file',*RESUME_TYPES['resume_docx'],original)
        assert len(q.field_actions(task['task_id']))==3 and len(q.run_attempts(task['task_id']))==1
        with q.tx() as db:
            assert db.execute('SELECT COUNT(*) FROM preparation_resume_uploads').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM preparation_resume_stages').fetchone()[0]==3
        assert len(set(owner_threads))==1 and threading.get_ident() not in owner_threads
    finally:
        closed=controller.shutdown(timeout=10)
        server.shutdown();server.server_close();serving.join()
        assert closed
    assert controller.status()['status']=='CLOSED' and not q.preparation_in_flight()
