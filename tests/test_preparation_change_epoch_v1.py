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


def test_only_owner_coordinator_composes_classifier_and_live_forwarding_stays_disabled():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for path in (root/'executor').rglob('*.py'):
        # The sole owner-thread composition is now wired, but production
        # constructs it with unavailable forwarding admission (checked below).
        if path.relative_to(root).as_posix() in {'executor/preparation/change_epoch.py',
                'executor/preparation/request_classifier.py','executor/preparation/human_review.py'}:continue
        source=path.read_text()
        assert 'from .change_epoch' not in source
        assert 'from executor.preparation.change_epoch' not in source
        assert 'PreDocumentChangeEpoch' not in source
        assert 'RetainedXHRClassifier' not in source
        assert 'from .request_classifier' not in source
        assert 'from executor.preparation.request_classifier' not in source

    import ast
    coordinator=ast.parse((root/'executor/preparation/human_review.py').read_text())
    cls=next(node for node in coordinator.body if isinstance(node,ast.ClassDef) and node.name=='HumanReviewCoordinator')
    init=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='__init__')
    index=[arg.arg for arg in init.args.kwonlyargs].index('forwarding_admission')
    default=init.args.kw_defaults[index]
    assert isinstance(default,ast.Lambda) and isinstance(default.body,ast.Constant) and default.body.value is False
    child=ast.parse((root/'executor/preparation/private_child.py').read_text())
    constructions=[node for node in ast.walk(child) if isinstance(node,ast.Call)
                   and isinstance(node.func,ast.Name) and node.func.id=='HumanReviewCoordinator']
    assert len(constructions)==1 and len(constructions[0].args)==2 and constructions[0].keywords==[]
    owner,admission=constructions[0].args
    assert isinstance(owner,ast.Name) and owner.id=='owner'
    assert (isinstance(admission,ast.Attribute) and admission.attr=='admit'
            and isinstance(admission.value,ast.Name) and admission.value.id=='admission')
    locations=[]
    for path in (root/'executor').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node,ast.Call) and
                    (isinstance(node.func,ast.Name) and node.func.id=='HumanReviewCoordinator'
                     or isinstance(node.func,ast.Attribute) and node.func.attr=='HumanReviewCoordinator')):
                locations.append(path.relative_to(root).as_posix())
    assert locations==['executor/preparation/private_child.py']



@pytest.mark.parametrize('value',['EXTRA_REALM','GLOBAL_REFLECTION','PRIVATE_CANARY',{'value':'PRIVATE_CANARY'},None])
def test_research_diagnostic_is_finite_and_cannot_clear_refusal(value):
    context=Context();watch=PreDocumentChangeEpoch(context);page=Page(context);watch.seal()
    watch._invalidate();page.evaluate=lambda *_:value
    expected=value if value in ('EXTRA_REALM','GLOBAL_REFLECTION') else 'UNAVAILABLE'
    assert watch.diagnostic()==expected
    with pytest.raises(ChangeEpochConflict):watch.unchanged()
