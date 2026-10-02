"""Pure validation/lifecycle tests; no real browser or applicant values."""
from types import SimpleNamespace
import pytest
from executor.preparation.change_epoch import PreDocumentChangeEpoch, ChangeEpochConflict, validate_snapshot

GOOD={'format':'preparation-change-epoch-v1','epoch':7,'invalid':False,'sealed':True}

@pytest.mark.parametrize('patch',[
    {'epoch':True},{'epoch':-1},{'epoch':2**53-1},{'epoch':1.5},
    {'invalid':1},{'invalid':True},{'sealed':False},{'sealed':1},
    {'format':'other'},{'value':'synthetic-secret'},
])
def test_ambiguous_or_value_bearing_snapshot_refused(patch):
    with pytest.raises(ChangeEpochConflict):validate_snapshot({**GOOD,**patch},sealed=True)

def test_only_bounded_monotonic_number_returned():
    assert validate_snapshot(GOOD,sealed=True)==7

class Context:
    def __init__(self):self.pages=[];self.service_workers=[];self.events={};self.scripts=[]
    def on(self,name,callback):self.events[name]=callback
    def add_init_script(self,**args):self.scripts.append(args['script'])

class Page:
    def __init__(self,context):
        self.events={};self.frames=[object()];self.result={**GOOD,'sealed':False};self.context=context
        context.pages.append(self);context.events['page'](self)
    def on(self,name,callback):self.events[name]=callback
    def is_closed(self):return False
    def evaluate(self,source,args):
        assert 'value' not in source
        if args['method']=='seal':self.result['sealed']=True
        return dict(self.result)

def test_fresh_context_only_and_no_retrofit():
    context=Context();context.pages=[object()]
    with pytest.raises(ChangeEpochConflict):PreDocumentChangeEpoch(context)
    assert context.scripts==[]

@pytest.mark.parametrize('event',['frameattached','close','crash','framenavigated'])
def test_historical_native_event_is_not_repaired_by_current_single_frame(event):
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context)
    assert watch.seal()==7 and watch.unchanged()==7
    page.events[event](object())
    with pytest.raises(ChangeEpochConflict):watch.unchanged()

def test_another_page_removed_again_stays_invalid():
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context);watch.seal()
    other=Page(context);context.pages.remove(other)
    with pytest.raises(ChangeEpochConflict):watch.unchanged()

def test_epoch_change_cannot_be_resealed():
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context);watch.seal()
    page.result['epoch']=8
    with pytest.raises(ChangeEpochConflict):watch.unchanged()
    with pytest.raises(ChangeEpochConflict):watch.seal()

def test_renderer_early_seal_is_refused():
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context);page.result['sealed']=True
    with pytest.raises(ChangeEpochConflict):watch.seal()

@pytest.mark.parametrize('fault', ['epoch','malformed','exception','closed','frames','serviceworker'])
def test_read_failure_is_permanent_after_a_healthy_looking_retry(fault):
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context);watch.seal()
    evaluate=page.evaluate
    if fault=='epoch':page.result['epoch']=8
    if fault=='malformed':page.result['value']='SYNTHETIC_PRIVATE_CANARY'
    if fault=='exception':
        def fail(*_):raise RuntimeError('SYNTHETIC_PRIVATE_CANARY')
        page.evaluate=fail
    if fault=='closed':page.is_closed=lambda:True
    if fault=='frames':page.frames.append(object())
    if fault=='serviceworker':context.service_workers.append(object())
    with pytest.raises(ChangeEpochConflict) as error:watch.unchanged()
    assert str(error.value)=='preparation_change_epoch_conflict'
    assert error.value.__suppress_context__ is True
    page.result=dict(GOOD);page.evaluate=evaluate;page.is_closed=lambda:False
    page.frames=page.frames[:1];context.service_workers=[]
    with pytest.raises(ChangeEpochConflict):watch.unchanged()
    with pytest.raises(ChangeEpochConflict):watch.seal()

@pytest.mark.parametrize('event',['serviceworker','close'])
def test_context_lifecycle_loss_is_permanent(event):
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context);watch.seal()
    context.events[event](object())
    with pytest.raises(ChangeEpochConflict):watch.unchanged()


def test_failed_initial_seal_cannot_be_retried_after_repair():
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context)
    page.result['invalid']=True
    with pytest.raises(ChangeEpochConflict):watch.seal()
    page.result['invalid']=False
    with pytest.raises(ChangeEpochConflict):watch.seal()


def test_observer_cannot_become_a_production_entrypoint():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for path in (root/'executor').rglob('*.py'):
        if path.name=='change_epoch.py':continue
        source=path.read_text()
        assert 'from .change_epoch' not in source
        assert 'from executor.preparation.change_epoch' not in source
        assert 'PreDocumentChangeEpoch' not in source
