"""Unregistered, value-free pre-document change observer research component.

This does not classify a request, authorize a transmission, or establish human
intent. It must be the first installer in a fresh, exclusively owned context.
The native owner must independently fence process/frame/document identity.
"""
from __future__ import annotations

import json
import secrets

from .qiyunfang import ROOT


# Only booleans and a monotonic count cross the renderer boundary. Setter
# arguments remain in the renderer and are neither inspected nor retained.
# This conservative research observer freezes its instrumentation, and any
# document mutation or reflection write after sealing revokes equality. It is
# not a general sandbox for arbitrary hostile JavaScript or browser extensions.
SCRIPT = r"""options => {
  'use strict';
  const {key,rootSelector} = options;
  const define = Object.defineProperty;
  const descriptor = Object.getOwnPropertyDescriptor;
  const getPrototype = Object.getPrototypeOf;
  const create = Object.create;
  const freeze = Object.freeze;
  const apply = Reflect.apply;
  const query = Document.prototype.querySelectorAll;
  const queryElement = Element.prototype.querySelector;
  const item = NodeList.prototype.item;
  const listLength = descriptor(NodeList.prototype,'length').get;
  const nodeType = descriptor(Node.prototype,'nodeType').get;
  const tagName = descriptor(Element.prototype,'tagName').get;
  const eventTarget = descriptor(Event.prototype,'target').get;
  const listen = EventTarget.prototype.addEventListener;
  const recordType = descriptor(MutationRecord.prototype,'type').get;
  const addedNodes = descriptor(MutationRecord.prototype,'addedNodes').get;
  const removedNodes = descriptor(MutationRecord.prototype,'removedNodes').get;
  const MAX = Number.MAX_SAFE_INTEGER;
  let epoch = 0, invalid = false, sealed = false, root = null, controls = [];
  const watched = [], chains = [];
  const nativeControls = [['INPUT',HTMLInputElement.prototype],['TEXTAREA',HTMLTextAreaElement.prototype],
    ['SELECT',HTMLSelectElement.prototype],['BUTTON',HTMLButtonElement.prototype]];
  const own = (object,name) => { const entry=descriptor(object,name); return entry ? entry.value : undefined; };
  const length = list => apply(listLength,list,[]);
  const nodes = selector => {
    const list = apply(query,document,[selector]), result = [];
    for (let i=0; i<length(list); i++) result[i] = apply(item,list,[i]);
    return result;
  };
  const bump = () => { if (epoch >= MAX) invalid = true; else epoch += 1; };
  const damage = () => { invalid = true; bump(); };
  const hasRealm = node => {
    if (apply(nodeType,node,[]) !== 1) return false;
    const tag = apply(tagName,node,[]);
    return tag === 'IFRAME' || tag === 'FRAME' || tag === 'OBJECT' || tag === 'EMBED' ||
      apply(queryElement,node,['iframe,frame,object,embed']) !== null;
  };
  const mutate = records => {
    try {
      for (let i=0; i<records.length; i++) {
        const record = records[i];
        if (sealed) bump();
        if (apply(recordType,record,[]) !== 'childList') continue;
        const added = apply(addedNodes,record,[]), removed = apply(removedNodes,record,[]);
        for (let j=0; j<length(added); j++) if (hasRealm(apply(item,added,[j]))) damage();
        for (let j=0; j<length(removed); j++) if (hasRealm(apply(item,removed,[j]))) damage();
      }
    } catch (_) { damage(); }
  };
  const observer = new MutationObserver(mutate);
  const takeRecords = MutationObserver.prototype.takeRecords;
  apply(MutationObserver.prototype.observe,observer,
    [document,{subtree:true,childList:true,attributes:true,characterData:true}]);
  const drain = () => mutate(apply(takeRecords,observer,[]));
  const track = (proto,name,current) => {
    watched[watched.length] = [proto,name,current];
    chains[chains.length] = [proto,getPrototype(proto)];
  };
  // Python resolves this binding for every read; a writable globalThis alias
  // would allow page code to substitute a forged healthy observer object.
  const globalBinding = {value:globalThis,writable:false,enumerable:false,configurable:false};
  define(globalThis,'globalThis',globalBinding); track(globalThis,'globalThis',globalBinding);
  const wrapSetter = (proto,name) => {
    const original = descriptor(proto,name);
    if (!original || typeof original.set !== 'function' || !original.configurable) { damage(); return; }
    const setter = function(value) {
      // A radio peer outside the root can silently uncheck an inside control.
      // Record all native control writes, without reading the argument or value.
      if (sealed) bump();
      return apply(original.set,this,[value]);
    };
    const current = {get:original.get,set:setter,enumerable:original.enumerable,configurable:false};
    define(proto,name,current); track(proto,name,current);
  };
  const inputNames = ['value','valueAsNumber','valueAsDate','checked','defaultValue','defaultChecked','files'];
  for (let i=0; i<inputNames.length; i++) wrapSetter(HTMLInputElement.prototype,inputNames[i]);
  wrapSetter(HTMLTextAreaElement.prototype,'value');
  wrapSetter(HTMLTextAreaElement.prototype,'defaultValue');
  wrapSetter(HTMLSelectElement.prototype,'value');
  wrapSetter(HTMLSelectElement.prototype,'selectedIndex');
  wrapSetter(HTMLOptionElement.prototype,'selected');
  wrapSetter(HTMLOptionElement.prototype,'defaultSelected');
  const wrapMethod = (proto,name,alwaysInvalid=false) => {
    const original = descriptor(proto,name);
    if (!original || typeof original.value !== 'function' || !original.configurable) { damage(); return; }
    const method = function(...args) {
      if (alwaysInvalid) damage(); else if (sealed) bump();
      return apply(original.value,this,args);
    };
    const current = {value:method,writable:false,enumerable:original.enumerable,configurable:false};
    define(proto,name,current); track(proto,name,current);
  };
  wrapMethod(HTMLInputElement.prototype,'setRangeText');
  wrapMethod(HTMLInputElement.prototype,'stepUp');
  wrapMethod(HTMLInputElement.prototype,'stepDown');
  wrapMethod(HTMLTextAreaElement.prototype,'setRangeText');
  wrapMethod(HTMLFormElement.prototype,'reset');
  // These can discard listeners or hide new realms from document observation.
  wrapMethod(Document.prototype,'open',true);
  wrapMethod(Element.prototype,'attachShadow',true);
  wrapMethod(globalThis,'open',true);
  const reflectionWrite = target => {
    if (sealed) { damage(); return; }
    // Before sealing, page code may define normal object properties. Native
    // instrumentation/prototype mutations are unsupported even if restored.
    for (let i=0; i<chains.length; i++) if (target === chains[i][0]) { damage(); return; }
  };
  const wrapReflection = (proto,name) => {
    const original = descriptor(proto,name);
    if (!original || typeof original.value !== 'function' || !original.configurable) { damage(); return; }
    const method = function(...args) { reflectionWrite(args[0]); return apply(original.value,this,args); };
    const current = {value:method,writable:false,enumerable:original.enumerable,configurable:false};
    define(proto,name,current); track(proto,name,current);
  };
  wrapReflection(Object,'defineProperty');
  wrapReflection(Object,'defineProperties');
  wrapReflection(Object,'setPrototypeOf');
  wrapReflection(Reflect,'defineProperty');
  wrapReflection(Reflect,'deleteProperty');
  wrapReflection(Reflect,'setPrototypeOf');
  const legacyNames = ['__defineGetter__','__defineSetter__'];
  for (let i=0; i<legacyNames.length; i++) {
    const name = legacyNames[i];
    const original = descriptor(Object.prototype,name);
    const method = function(...args) { reflectionWrite(this); return apply(original.value,this,args); };
    const current = {value:method,writable:false,enumerable:false,configurable:false};
    define(Object.prototype,name,current); track(Object.prototype,name,current);
  }
  const protoDescriptor = descriptor(Object.prototype,'__proto__');
  const protoSetter = function(value) { reflectionWrite(this); return apply(protoDescriptor.set,this,[value]); };
  const currentProto = {get:protoDescriptor.get,set:protoSetter,enumerable:false,configurable:false};
  define(Object.prototype,'__proto__',currentProto); track(Object.prototype,'__proto__',currentProto);
  const inputEvent = event => {
    try { apply(eventTarget,event,[]); if (sealed) bump(); }
    catch (_) { damage(); }
  };
  // Earliest window capture sees native edits before later page listeners can
  // stop propagation on the path to document. The first-installer precondition
  // is essential; a document-only observer is not sufficient.
  apply(listen,globalThis,['input',inputEvent,true]);
  apply(listen,globalThis,['change',inputEvent,true]);
  apply(listen,globalThis,['reset',inputEvent,true]);
  apply(listen,document,['input',inputEvent,true]);
  apply(listen,document,['change',inputEvent,true]);
  apply(listen,document,['reset',inputEvent,true]);
  const integrity = () => {
    drain();
    for (let i=0; i<watched.length; i++) {
      const entry = watched[i], expected = entry[2], current = descriptor(entry[0],entry[1]);
      if (!current || own(current,'get') !== own(expected,'get') || own(current,'set') !== own(expected,'set') ||
          own(current,'value') !== own(expected,'value') || own(current,'writable') !== own(expected,'writable') ||
          own(current,'configurable') !== own(expected,'configurable') || own(current,'enumerable') !== own(expected,'enumerable')) damage();
    }
    for (let i=0; i<chains.length; i++) if (getPrototype(chains[i][0]) !== chains[i][1]) damage();
    if (nodes('iframe,frame,object,embed').length) damage();
    if (sealed) {
      const roots = nodes(rootSelector);
      if (roots.length !== 1 || roots[0] !== root) damage();
      const current = nodes(rootSelector+' input,'+rootSelector+' textarea,'+rootSelector+' select,'+rootSelector+' button');
      if (current.length !== controls.length) damage();
      for (let i=0; i<controls.length; i++) {
        if (current[i] !== controls[i]) damage();
        for (let j=0; j<inputNames.length; j++) if (descriptor(controls[i],inputNames[j])) damage();
        const extraNames = ['selectedIndex','setRangeText','stepUp','stepDown','reset'];
        for (let j=0; j<extraNames.length; j++) if (descriptor(controls[i],extraNames[j])) damage();
        const tag = apply(tagName,controls[i],[]);
        for (let j=0; j<nativeControls.length; j++) {
          if (nativeControls[j][0] === tag && getPrototype(controls[i]) !== nativeControls[j][1]) damage();
        }
      }
    }
  };
  const snapshot = () => {
    try { integrity(); } catch (_) { damage(); }
    const result = create(null);
    result.format = 'preparation-change-epoch-v1'; result.epoch = epoch;
    result.invalid = invalid; result.sealed = sealed;
    return result;
  };
  const seal = () => {
    try {
      integrity();
      if (sealed || invalid) { damage(); return snapshot(); }
      const roots = nodes(rootSelector);
      if (roots.length !== 1) { damage(); return snapshot(); }
      root = roots[0];
      controls = nodes(rootSelector+' input,'+rootSelector+' textarea,'+rootSelector+' select,'+rootSelector+' button');
      if (!controls.length) { damage(); return snapshot(); }
      sealed = true;
      return snapshot();
    } catch (_) { damage(); return snapshot(); }
  };
  // Public read/seal calls cannot reset the epoch or clear invalidation. Early
  // hostile sealing can deny service only; the native owner refuses resealing.
  if (descriptor(globalThis,key)) return;
  define(globalThis,key,{value:freeze({snapshot,seal}),writable:false,configurable:false});
}
"""


class ChangeEpochConflict(RuntimeError):
    def __init__(self):
        super().__init__('preparation_change_epoch_conflict')


def validate_snapshot(value, *, sealed):
    if (type(value) is not dict or set(value) != {'format','epoch','invalid','sealed'}
            or value['format'] != 'preparation-change-epoch-v1'
            or type(value['epoch']) is not int or not 0 <= value['epoch'] < 2**53-1
            or value['invalid'] is not False or value['sealed'] is not sealed):
        raise ChangeEpochConflict()
    return value['epoch']


class PreDocumentChangeEpoch:
    """Test/research installer. Never imported by production session/controller.

    The caller owns a fresh context with no earlier untrusted init scripts.
    No page value or request body is read. Events and read failures are
    historical: removing a frame or repairing the DOM cannot refund trust.
    This intentionally conservative prototype freezes native instrumentation;
    page frameworks requiring descriptor replacement are unsupported.
    """
    def __init__(self, context):
        if context.pages or context.service_workers:
            raise ChangeEpochConflict()
        self.context = context
        self.key = '__jae_epoch_' + secrets.token_hex(16)
        self.page = None
        self.invalid = False
        self.baseline = None
        context.add_init_script(script='('+SCRIPT+')('+json.dumps({'key':self.key,'rootSelector':ROOT})+');')
        context.on('page', self._page_created)
        context.on('serviceworker', self._invalidate)
        context.on('close', self._invalidate)

    def _page_created(self, page):
        if self.page is not None:
            self.invalid = True
            return
        self.page = page
        page.on('frameattached', self._invalidate)
        page.on('close', self._invalidate)
        page.on('crash', self._invalidate)
        page.on('framenavigated', lambda _: self._invalidate() if self.baseline is not None else None)

    def _invalidate(self, *_):
        self.invalid = True

    def _conflict(self):
        self.invalid = True
        raise ChangeEpochConflict() from None

    def _read(self, method):
        try:
            if (self.invalid or self.page is None or self.page.is_closed()
                    or self.context.pages != [self.page] or len(self.page.frames) != 1
                    or self.context.service_workers):
                self._conflict()
            result = self.page.evaluate('({key,method}) => globalThis[key][method]()', {'key':self.key,'method':method})
            if self.invalid:
                self._conflict()
            return result
        except Exception:
            self._conflict()

    def seal(self):
        try:
            if self.baseline is not None:
                self._conflict()
            validate_snapshot(self._read('snapshot'), sealed=False)
            self.baseline = validate_snapshot(self._read('seal'), sealed=True)
            return self.baseline
        except Exception:
            self._conflict()

    def unchanged(self):
        try:
            if self.baseline is None:
                self._conflict()
            current = validate_snapshot(self._read('snapshot'), sealed=True)
            if current != self.baseline:
                self._conflict()
            return current
        except Exception:
            self._conflict()
