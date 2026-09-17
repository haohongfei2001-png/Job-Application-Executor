from __future__ import annotations
import copy, json, time, mimetypes
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
from ..browser import ensure_chrome, CDP

BASE='https://xiaoyuan.zhipin.com'
SAVE='/wapi/zpxzats/geek/volunteer/saveResumeData'

class SchneiderBossAdapter:
    def __init__(self, job_url: str):
        self.job_url=job_url; self.pw=self.browser=self.ctx=None
        self.capture_data={}; self.headers={}

    def __enter__(self):
        ensure_chrome(); self.pw=sync_playwright().start()
        self.browser=self.pw.chromium.connect_over_cdp(CDP,timeout=10000); self.ctx=self.browser.contexts[0]
        self._capture(); return self

    def __exit__(self,*_):
        if self.pw:
            try:self.pw.stop()
            except Exception:pass

    def _capture(self):
        p=self.ctx.new_page(); seed={}
        def req(r):
            if '/wapi/zpxzats/geek/' in r.url and not self.headers:
                self.headers=r.headers
        def resp(r):
            if 'getUserInfo' in r.url:
                try: seed['user']=r.json().get('zpData')
                except Exception: pass
        p.on('request',req); p.on('response',resp)
        sep='&' if '?' in self.job_url else '?'
        try:p.goto(self.job_url+sep+'_adapter='+str(int(time.time()*1000)),wait_until='commit',timeout=30000)
        except Exception:pass
        for _ in range(120):
            if seed.get('user') and self.headers: break
            p.wait_for_timeout(100)
        user=seed.get('user') or {}
        if not user.get('login'): raise RuntimeError('BOSS campus login required')
        if not self.headers: raise RuntimeError('failed to capture authenticated BOSS request headers')
        self.capture_data['user']=user
        job_id=user.get('encryptJobId') or parse_qs(urlparse(self.job_url).query).get('encryptJobId',[''])[0]
        project_id=user.get('encryptProjectId')
        if not job_id or not project_id: raise RuntimeError('missing BOSS job/project identifiers')
        self.capture_data['job']=self._get('/wapi/zpxzats/geek/volunteer/getJobDetail',{'encryptJobId':job_id})
        self.capture_data['template']=self._get('/wapi/zpxzats/geek/volunteer/getResumeTemplateV2',{'encryptProjectId':project_id})
        self.capture_data['cities']=self._get('/wapi/zpxzats/geek/common/getATSCity')
        self.capture_data['schools']=self._get('/wapi/zpxzats/geek/common/getATSSchool',{'schoolType':0})
        self.capture_data['majors']=self._get('/wapi/zpxzats/geek/common/getMajorConfig')
        self.capture_data['degrees']=self._get('/wapi/zpxzats/geek/common/getSelectDegree',{'highest':1})
        self.capture_data['resume']=self._get_resume(job_id,project_id)

    def _get(self,path:str,params:dict|None=None):
        params=dict(params or {}); params['_']=int(time.time()*1000)
        qs='&'.join(f'{k}={v}' for k,v in params.items())
        resp=self.ctx.request.get(BASE+path+('?' + qs if qs else ''),headers=self._api_headers(),timeout=30000)
        out=resp.json()
        if resp.status!=200 or out.get('code')!=0:
            raise RuntimeError(f'BOSS GET failed {path}: HTTP {resp.status} {out}')
        return out.get('zpData')

    def _get_resume(self,job_id:str,project_id:str):
        payload={'encryptJobId':job_id,'encryptProjectId':project_id,'backType':0,'recommendCode':''}
        resp=self.ctx.request.post(BASE+f'/wapi/zpxzats/geek/volunteer/getResumeData?_={int(time.time()*1000)}',
                                   headers=self._api_headers(),data=payload,timeout=30000)
        out=resp.json()
        if resp.status!=200 or out.get('code')!=0 or not isinstance(out.get('zpData'),dict):
            raise RuntimeError(f'getResumeData failed: HTTP {resp.status} {out}')
        return out['zpData']

    @property
    def resume(self): return self.capture_data['resume']
    @property
    def template(self): return self.capture_data['template']
    @property
    def job(self): return self.capture_data['job']

    def _api_headers(self):
        allow={'zp_token','x-requested-with','x-download-domain','content-type','x-anti-request-token','referer','traceid','accept'}
        return {k:v for k,v in self.headers.items() if k.lower() in allow}

    def _serialize_data_for_save(self,data:dict):
        out=copy.deepcopy(data)
        for group_id in ('BASE_INFO','ATTACHMENT'):
            group=self._groups().get(group_id) or {}
            attachment_ids={f['id'] for f in group.get('fields',[]) if f.get('formType')=='attachment'}
            for row in out.get(group_id) or []:
                for field_id in attachment_ids:
                    vals=row.get(field_id) or []
                    normalized=[]
                    for item in vals:
                        if isinstance(item,str): normalized.append(item)
                        elif isinstance(item,dict) and item.get('encryptId'): normalized.append(str(item['encryptId']))
                    row[field_id]=normalized
        return out

    def save_data(self,data:dict):
        r=self.resume; payload={'encryptJobId':r['encryptJobId'],'encryptProjectId':r['encryptProjectId'],'data':self._serialize_data_for_save(data)}
        resp=self.ctx.request.post(BASE+SAVE+f'?_={int(time.time()*1000)}',headers=self._api_headers(),data=payload,timeout=30000)
        out=resp.json()
        if resp.status!=200 or out.get('code')!=0 or out.get('zpData') is not True:
            raise RuntimeError(f'saveResumeData failed: HTTP {resp.status} {out}')
        return out

    def refresh_resume(self):
        r=self.resume
        self.capture_data['resume']=self._get_resume(r['encryptJobId'],r['encryptProjectId'])
        return self.capture_data['resume']

    def upload_file(self, field_id:str, file_path:str|Path):
        meta=self.validate_asset(field_id,file_path)
        path=Path(meta['path'])
        headers={k:v for k,v in self._api_headers().items() if k.lower()!='content-type'}
        headers['X-Download-Domain']=BASE
        mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        multipart={
            'fieldId':field_id, 'source':'0',
            'file':{'name':path.name,'mimeType':mime,'buffer':path.read_bytes()},
        }
        resp=self.ctx.request.post(BASE+'/wapi/zpxzats/geek/volunteer/uploadFile',headers=headers,multipart=multipart,timeout=60000)
        out=resp.json()
        if resp.status!=200 or out.get('code')!=0 or not out.get('zpData'):
            raise RuntimeError(f'uploadFile failed for {field_id}: HTTP {resp.status} {out}')
        return out['zpData']

    def _groups(self): return {g['groupId']:g for g in self.template['rtFieldGroupList']}
    @staticmethod
    def _blank_value(f):
        typ=f.get('formType')
        return [] if typ in {'select','cascader','attachment','other','dateRange'} else ''
    def blank_record(self,group_id):
        return {f['id']:self._blank_value(f) for f in self._groups()[group_id].get('fields',[])}
    def ensure_record(self,data,group_id):
        if not data.get(group_id): data[group_id]=[self.blank_record(group_id)]
        return data[group_id][0]

    def city_path(self,*names):
        nodes=self.capture_data.get('cities') or [] ; out=[]
        for name in names:
            hit=next((x for x in nodes if x.get('name')==name),None)
            if not hit: raise KeyError(f'city node not found: {name}')
            out.append(hit['code']); nodes=hit.get('subLevelModelList') or []
        return out

    def build_safe_data(self,app:dict):
        data=copy.deepcopy(self.resume['data'])
        base=self.ensure_record(data,'BASE_INFO'); contact=self.ensure_record(data,'CONTACT')
        base.update({
            'name':app['full_name'],'gender':['1' if app['gender']=='男' else '0'],
            'birthday':app['birthday_month'].replace('-','/'),'credentialsType':['0'],'atsIdCardNo':app['id_card'],
            'residentCity':self.city_path(*app['resident_city_path']),
            'livingCity':self.city_path(*app['living_city_path']),
            'highestDegree':['204'],'highestDegreeGraduationDate':app['graduation_month'].replace('-','/'),
            'hasRecommendCode':['0'],'recommendCode':''})
        contact['atsPhone']=app['phone']; contact['email']=app['email']
        contact.setdefault('extra',{'atsPhone':{'regionCode':'+86','regionName':'中国大陆'}})
        edu=[]
        for x in app.get('education',[]):
            rec=self.blank_record('EDUCATION')
            rec.update({'educationBackground':[x['education_code']],'schoolName':x['school'],
                        'degree':[x['degree_code']],'major':[x['major']],
                        'timeSlot':[x['start_month'].replace('-','/'),x['end_month'].replace('-','/')]})
            if x.get('rank_code'): rec['field-1721014512580']=[x['rank_code']]
            edu.append(rec)
        if edu:data['EDUCATION']=edu
        eng=self.ensure_record(data,'ENGLISH')
        eng['field-1721026726229']=['option-1721026726230']
        eng['field-1721026990799']=['option-1721026990800']
        att=self.ensure_record(data,'ATTACHMENT')
        att['adjust']=['1']; att['field-1721029434662']=['option-1721029434663']
        att['field-1721029452878']=['option-1721029452879']
        att['field-1721029594740']=['option-1721029594740']
        return data


    def field(self, group_id:str, field_id:str):
        group=self._groups().get(group_id) or {}
        return next((f for f in group.get('fields',[]) if f.get('id')==field_id),None)

    def select_value(self, group_id:str, field_id:str, label:str):
        f=self.field(group_id,field_id)
        if not f: raise KeyError(f'field not found: {group_id}.{field_id}')
        hit=next((o for o in (f.get('options') or []) if o.get('label')==label),None)
        if not hit: raise KeyError(f'option not found: {field_id}={label}')
        return str(hit['value'])

    def validate_asset(self, field_id:str, file_path:str|Path):
        path=Path(file_path).expanduser().resolve()
        if not path.is_file(): raise FileNotFoundError(path)
        f=None
        for gid in ('BASE_INFO','ATTACHMENT'):
            f=self.field(gid,field_id)
            if f: break
        if not f or f.get('formType')!='attachment': raise KeyError(f'attachment field not found: {field_id}')
        ext=path.suffix.lstrip('.').upper()
        allowed={str(x).upper() for x in (f.get('fileTypes') or [])}
        if allowed and ext not in allowed: raise ValueError(f'{field_id} does not allow .{ext}; allowed={sorted(allowed)}')
        max_mb=float(f.get('maxSize') or 0)
        size_mb=path.stat().st_size/(1024*1024)
        if max_mb and size_mb>max_mb: raise ValueError(f'{field_id} file is {size_mb:.2f}MB > {max_mb:.2f}MB')
        return {'path':str(path),'name':path.name,'ext':ext,'size_bytes':path.stat().st_size,'max_mb':max_mb}

    @staticmethod
    def _file_id(file_obj):
        if isinstance(file_obj,str): return file_obj
        if isinstance(file_obj,dict) and file_obj.get('encryptId'): return str(file_obj['encryptId'])
        raise ValueError('uploaded file object is missing encryptId')

    def attach_uploaded(self, data:dict, field_id:str, file_obj:dict|str):
        if field_id=='certificatePhoto':
            self.ensure_record(data,'BASE_INFO')['certificatePhoto']=[self._file_id(file_obj)]
        elif field_id=='bossAttachment':
            self.ensure_record(data,'ATTACHMENT')['bossAttachment']=[self._file_id(file_obj)]
        else:
            raise KeyError(f'unsupported managed attachment field: {field_id}')
        return data

    def apply_decisions(self, data:dict, decisions:dict):
        base=self.ensure_record(data,'BASE_INFO'); att=self.ensure_record(data,'ATTACHMENT')
        if decisions.get('work_city_path'):
            base['field-1721014054734']=self.city_path(*decisions['work_city_path'])
        if decisions.get('interview_city_path'):
            base['field-1721014095457']=self.city_path(*decisions['interview_city_path'])
        salary=decisions.get('salary')
        if salary is not None:
            if salary=='negotiable':
                base['bossSalary']=[0,0]
            else:
                if not (isinstance(salary,list) and len(salary)==2 and all(isinstance(x,(int,float)) for x in salary)):
                    raise ValueError('salary must be "negotiable" or [low_k, high_k]')
                low,high=int(salary[0]),int(salary[1])
                if low<=0 or high<=low: raise ValueError('salary range must satisfy 0 < low < high')
                base['bossSalary']=[low,high]
        for field_id,answer in (decisions.get('compliance') or {}).items():
            if answer is None: continue
            if not isinstance(answer,bool): raise ValueError(f'compliance answer must be boolean: {field_id}')
            att[field_id]=[self.select_value('ATTACHMENT',field_id,'是' if answer else '否')]
        return data

    def unresolved_required(self,data):
        unresolved=[]
        for g in self.template['rtFieldGroupList']:
            rows=data.get(g['groupId']) or []
            for f in g.get('fields',[]):
                if not f.get('required'): continue
                # related fields such as other document type / referral code are conditionally required
                if f['id'] in {'otherCredentialsType','otherCredentialsNo','recommendCode','otherDegree'}: continue
                vals=[r.get(f['id']) for r in rows] if rows else []
                if not vals or all(v in ('',None,[]) for v in vals):
                    unresolved.append({'group':g['groupName'],'id':f['id'],'name':f.get('name') or f['id'],'formType':f.get('formType')})
        return unresolved

    def probe_summary(self):
        return {'login':self.capture_data['user'].get('login'),'project':self.capture_data['user'].get('projectName'),
                'job':self.job.get('fullJobName'),'category':self.job.get('jobCategory'),'location':self.job.get('addressVO'),
                'resumeSubmit':self.resume.get('resumeSubmit'),'hasResume':self.capture_data['user'].get('hasResume')}

def load_schneider_profile(path:Path): return json.loads(path.read_text(encoding='utf-8'))
