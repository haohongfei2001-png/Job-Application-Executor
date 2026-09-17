from __future__ import annotations
import argparse,json
from pathlib import Path
from .engine import Executor
from .runtime import load_runtime,save_runtime
from .settings import load_settings,save_settings
from .submit import submit_current
from .target_resolver import resolve,candidate_dicts
from .state import RuntimeState,RunState
from .adapters.schneider_boss import SchneiderBossAdapter,load_schneider_profile

ROOT=Path.home()/"Job-Application-Executor"
SCH_PROFILE=ROOT/"config/schneider-profile.json"
SCH_DECISIONS=ROOT/"config/schneider-decisions.json"

def _sch_job(arg_job=None):
    rt=load_runtime(); job=arg_job or rt.job_url
    if not job or 'xiaoyuan.zhipin.com/volunteer/' not in job: raise RuntimeError('current job is not a BOSS campus application URL')
    return rt,job

def _sch_profile(path=None):
    p=Path(path).expanduser().resolve() if path else SCH_PROFILE
    if not p.is_file(): raise FileNotFoundError(p)
    return p,load_schneider_profile(p)

def _update_sch_runtime(rt,job,unresolved):
    rt.job_url=job; rt.active_url=job; rt.unresolved_fields=unresolved
    rt.final_submit_detected=False
    rt.state=RunState.WAITING_USER_INPUT if unresolved else RunState.WAITING_USER_CONFIRMATION
    save_runtime(rt)

def main():
    ap=argparse.ArgumentParser(prog='application-executor'); sub=ap.add_subparsers(dest='cmd',required=True)
    cfg=sub.add_parser('configure'); cfg.add_argument('--resume',required=True); cfg.add_argument('--profile',required=True)
    run=sub.add_parser('run'); run.add_argument('--job-url',required=True); run.add_argument('--resume'); run.add_argument('--profile')
    res=sub.add_parser('resolve'); res.add_argument('--company',required=True); res.add_argument('--title',required=True); res.add_argument('--location',default='China')
    sel=sub.add_parser('select'); sel.add_argument('--job-id',required=True)
    sub.add_parser('resume'); sub.add_parser('status'); sub.add_parser('review')
    submit=sub.add_parser('submit'); submit.add_argument('--confirm',required=True)
    for name in ('schneider-probe','schneider-plan','schneider-save-safe'):
        p=sub.add_parser(name); p.add_argument('--job-url'); p.add_argument('--profile')
    up=sub.add_parser('schneider-upload-assets'); up.add_argument('--job-url'); up.add_argument('--profile'); up.add_argument('--photo'); up.add_argument('--resume'); up.add_argument('--confirm',required=True)
    comp=sub.add_parser('schneider-complete'); comp.add_argument('--job-url'); comp.add_argument('--profile'); comp.add_argument('--decisions',default=str(SCH_DECISIONS))
    a=ap.parse_args()
    if a.cmd=='configure':
        r=str(Path(a.resume).expanduser().resolve()); p=str(Path(a.profile).expanduser().resolve())
        if not Path(r).is_file(): raise FileNotFoundError(r)
        if not Path(p).is_file(): raise FileNotFoundError(p)
        s=load_settings(); s.update({'resume_path':r,'profile_path':p}); save_settings(s); print(json.dumps(s,ensure_ascii=False,indent=2)); return
    if a.cmd=='resolve':
        items=resolve(a.company,a.title,a.location); exact=[x for x in items if x.exact_title]; pool=exact or items[:10]
        rt=RuntimeState(state=RunState.TARGET_SELECTION,target_query={'company':a.company,'title':a.title,'location':a.location},candidates=candidate_dicts(pool))
        if len(pool)==1: rt.job_url=pool[0].job_url
        save_runtime(rt); print(rt.model_dump_json(indent=2)); return
    if a.cmd=='select':
        rt=load_runtime(); match=next((x for x in rt.candidates if str(x.get('job_id'))==str(a.job_id)),None)
        if not match: raise RuntimeError(f'job id {a.job_id} is not in current candidates')
        rt.job_url=match['job_url']; rt.state=RunState.IDLE; rt.candidates=[match]; save_runtime(rt); print(rt.model_dump_json(indent=2)); return
    if a.cmd=='status': print(load_runtime().model_dump_json(indent=2)); return
    if a.cmd=='review':
        rt=load_runtime(); print(json.dumps({'state':rt.state,'active_url':rt.active_url,'resume_path':rt.resume_path,'filled_fields':rt.filled_fields,'unresolved_fields':rt.unresolved_fields,'final_submit_detected':rt.final_submit_detected},ensure_ascii=False,indent=2)); return
    if a.cmd=='submit': print(submit_current(a.confirm).model_dump_json(indent=2)); return
    if a.cmd.startswith('schneider-'):
        rt,job=_sch_job(getattr(a,'job_url',None)); profile_path,app=_sch_profile(getattr(a,'profile',None))
        settings=load_settings()
        with SchneiderBossAdapter(job) as ad:
            if ad.resume.get('resumeSubmit'): raise RuntimeError('application is already submitted; refusing to edit')
            if a.cmd=='schneider-probe': print(json.dumps(ad.probe_summary(),ensure_ascii=False,indent=2)); return
            data=ad.build_safe_data(app)
            if a.cmd=='schneider-plan':
                assets={}
                photo=settings.get('photo_path'); resume=settings.get('resume_path')
                if photo: assets['photo']=ad.validate_asset('certificatePhoto',photo)
                if resume: assets['resume']=ad.validate_asset('bossAttachment',resume)
                print(json.dumps({'summary':ad.probe_summary(),'unresolved':ad.unresolved_required(data),'assets':assets},ensure_ascii=False,indent=2)); return
            if a.cmd=='schneider-save-safe':
                ad.save_data(data); fresh=ad.refresh_resume()['data']; unresolved=ad.unresolved_required(fresh); _update_sch_runtime(rt,job,unresolved)
                print(json.dumps({'saved':True,'resumeSubmit':ad.resume.get('resumeSubmit'),'unresolved':unresolved},ensure_ascii=False,indent=2)); return
            if a.cmd=='schneider-upload-assets':
                if a.confirm!='UPLOAD_ASSETS': raise RuntimeError('refusing upload without --confirm UPLOAD_ASSETS')
                photo=a.photo or settings.get('photo_path'); resume=a.resume or settings.get('resume_path')
                if not photo or not resume: raise RuntimeError('photo and resume paths are required')
                current=ad.resume['data']; cur_photo=(current.get('BASE_INFO') or [{}])[0].get('certificatePhoto') or []; cur_resume=(current.get('ATTACHMENT') or [{}])[0].get('bossAttachment') or []
                po=cur_photo[0] if cur_photo else ad.upload_file('certificatePhoto',photo); ro=cur_resume[0] if cur_resume else ad.upload_file('bossAttachment',resume)
                ad.attach_uploaded(data,'certificatePhoto',po); ad.attach_uploaded(data,'bossAttachment',ro); ad.save_data(data)
                fresh=ad.refresh_resume()['data']; unresolved=ad.unresolved_required(fresh); _update_sch_runtime(rt,job,unresolved)
                print(json.dumps({'saved':True,'photo_count':len(fresh['BASE_INFO'][0].get('certificatePhoto') or []),'resume_count':len(fresh['ATTACHMENT'][0].get('bossAttachment') or []),'unresolved':unresolved},ensure_ascii=False,indent=2)); return
            if a.cmd=='schneider-complete':
                dp=Path(a.decisions).expanduser().resolve(); decisions=json.loads(dp.read_text(encoding='utf-8'))
                ad.apply_decisions(data,decisions); ad.save_data(data); fresh=ad.refresh_resume()['data']; unresolved=ad.unresolved_required(fresh); _update_sch_runtime(rt,job,unresolved)
                print(json.dumps({'saved':True,'resumeSubmit':ad.resume.get('resumeSubmit'),'unresolved':unresolved},ensure_ascii=False,indent=2)); return
    settings=load_settings(); rt=load_runtime()
    if a.cmd=='resume': job=rt.job_url; resume=rt.resume_path or settings.get('resume_path'); profile=rt.profile_path or settings.get('profile_path')
    else: job=a.job_url; resume=a.resume or settings.get('resume_path'); profile=a.profile or settings.get('profile_path')
    if not job or not resume or not profile: raise RuntimeError('job URL, resume path and profile path are required; run configure first or pass them explicitly')
    state=Executor(job,resume,profile,settings.get('auth_wait_seconds',900)).run(); print(state.model_dump_json(indent=2))
if __name__=='__main__': main()
