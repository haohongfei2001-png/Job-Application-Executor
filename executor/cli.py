from __future__ import annotations
import argparse,json
from pathlib import Path
from .engine import Executor
from .runtime import load_runtime
from .settings import load_settings,save_settings
from .submit import submit_current
from .target_resolver import resolve,candidate_dicts
from .runtime import save_runtime
from .state import RuntimeState,RunState

def main():
    ap=argparse.ArgumentParser(prog='application-executor'); sub=ap.add_subparsers(dest='cmd',required=True)
    cfg=sub.add_parser('configure'); cfg.add_argument('--resume',required=True); cfg.add_argument('--profile',required=True)
    run=sub.add_parser('run'); run.add_argument('--job-url',required=True); run.add_argument('--resume'); run.add_argument('--profile')
    res=sub.add_parser('resolve'); res.add_argument('--company',required=True); res.add_argument('--title',required=True); res.add_argument('--location',default='China')
    sel=sub.add_parser('select'); sel.add_argument('--job-id',required=True)
    sub.add_parser('resume'); sub.add_parser('status'); sub.add_parser('review')
    submit=sub.add_parser('submit'); submit.add_argument('--confirm',required=True)
    a=ap.parse_args()
    if a.cmd=='configure':
        r=str(Path(a.resume).expanduser().resolve()); p=str(Path(a.profile).expanduser().resolve())
        if not Path(r).is_file(): raise FileNotFoundError(r)
        if not Path(p).is_file(): raise FileNotFoundError(p)
        s=load_settings(); s.update({'resume_path':r,'profile_path':p}); save_settings(s); print(json.dumps(s,ensure_ascii=False,indent=2)); return
    if a.cmd=='resolve':
        items=resolve(a.company,a.title,a.location); exact=[x for x in items if x.exact_title]
        pool=exact or items[:10]
        rt=RuntimeState(state=RunState.TARGET_SELECTION,target_query={'company':a.company,'title':a.title,'location':a.location},candidates=candidate_dicts(pool))
        if len(pool)==1:
            rt.job_url=pool[0].job_url
        save_runtime(rt); print(rt.model_dump_json(indent=2)); return
    if a.cmd=='select':
        rt=load_runtime(); match=next((x for x in rt.candidates if str(x.get('job_id'))==str(a.job_id)),None)
        if not match: raise RuntimeError(f'job id {a.job_id} is not in current candidates')
        rt.job_url=match['job_url']; rt.state=RunState.IDLE; rt.candidates=[match]; save_runtime(rt); print(rt.model_dump_json(indent=2)); return
    if a.cmd=='status': print(load_runtime().model_dump_json(indent=2)); return
    if a.cmd=='review':
        rt=load_runtime(); print(json.dumps({'state':rt.state,'active_url':rt.active_url,'resume_path':rt.resume_path,'filled_fields':rt.filled_fields,'unresolved_fields':rt.unresolved_fields,'final_submit_detected':rt.final_submit_detected},ensure_ascii=False,indent=2)); return
    if a.cmd=='submit': print(submit_current(a.confirm).model_dump_json(indent=2)); return
    settings=load_settings(); rt=load_runtime()
    if a.cmd=='resume': job=rt.job_url; resume=rt.resume_path or settings.get('resume_path'); profile=rt.profile_path or settings.get('profile_path')
    else: job=a.job_url; resume=a.resume or settings.get('resume_path'); profile=a.profile or settings.get('profile_path')
    if not job or not resume or not profile: raise RuntimeError('job URL, resume path and profile path are required; run configure first or pass them explicitly')
    state=Executor(job,resume,profile).run(); print(state.model_dump_json(indent=2))
if __name__=='__main__': main()
