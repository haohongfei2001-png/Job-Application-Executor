"""Unregistered renderer classification bound to one retained native XHR Request.

No native request body is read. No method forwards a request, grants permission,
accepts a dialog, or registers a production callback. The network owner must keep
its independent deny-only boundary and park the exact Route before classification.
"""
from __future__ import annotations

import json
import secrets

from .change_epoch import PreDocumentChangeEpoch, validate_snapshot
from .human_request import FINAL_URL
from .qiyunfang import CONTRACT_URL, CONTRACT_ROLE, CONTRACT_VERSION, FIELDS
from .session import require_private_transport_environment


SCRIPT = r"""options => {
  'use strict';
  const {key,epochKey,nonce,finalUrl,documentUrl,role,fields} = options;
  const apply=Reflect.apply, ownKeys=Reflect.ownKeys, define=Object.defineProperty, descriptor=Object.getOwnPropertyDescriptor;
  const freeze=Object.freeze, create=Object.create, parse=JSON.parse;
  const decode=decodeURIComponent, slice=String.prototype.slice, lower=String.prototype.toLowerCase;
  const charCode=String.prototype.charCodeAt, stringIndex=String.prototype.indexOf, arrayIndex=Array.prototype.indexOf;
  const NativeURL=URL, href=descriptor(URL.prototype,'href').get;
  const requestUrl=descriptor(Request.prototype,'url').get;
  const NativePromise=Promise, promiseReject=Promise.reject, NativeError=Error;
  const listen=EventTarget.prototype.addEventListener, unlisten=EventTarget.prototype.removeEventListener;
  const maps=new WeakMap(), mapGet=WeakMap.prototype.get, mapSet=WeakMap.prototype.set;
  const proto=XMLHttpRequest.prototype;
  const nativeOpen=proto.open, nativeSend=proto.send, nativeHeader=proto.setRequestHeader, nativeAbort=proto.abort;
  let armed=false, invalid=false, capture=null, preflightActive=0;
  const bad=()=>{invalid=true;};
  const part=(s,a,b)=>apply(slice,s,[a,b]);
  const lowercase=s=>apply(lower,s,[]);
  const urlOf=value=>{
    if (typeof value !== 'string' || value.length>2048) throw 0;
    return apply(href,new NativeURL(value,documentUrl),[]);
  };
  const mimes=['application/x-www-form-urlencoded','application/x-www-form-urlencoded; charset=utf-8','application/x-www-form-urlencoded;charset=utf-8'];
  const mime=value=>typeof value==='string' && apply(arrayIndex,mimes,[lowercase(value)])!==-1;
  const decoded=s=>{
    let result='';
    for(let i=0;i<s.length;i++) result+=s[i]==='+'?' ':s[i];
    return decode(result);
  };
  const parameters=body=>{
    if(typeof body!=='string'||body.length>524288)throw 0;
    const result=create(null);let start=0;
    for(let i=0;i<=body.length;i++)if(i===body.length||body[i]==='&'){
      const entry=part(body,start,i);start=i+1;let equals=-1;
      for(let j=0;j<entry.length;j++)if(entry[j]==='='){equals=j;break;}
      if(equals<1)throw 0;
      const name=decoded(part(entry,0,equals));
      if(descriptor(result,name))throw 0;
      result[name]=part(entry,equals+1);
    }
    return result;
  };
  // The percent-decoded list stays renderer-local. This finite lexer skips
  // protected JSON string tokens, decoding only object keys and public role.
  // Duplicate object keys and duplicate field identities cannot collapse.
  const roleMatches=encoded=>{
    const text=decoded(encoded);let at=0,count=0,found=false;const ids=create(null);
    const ws=()=>{while(at<text.length&&(text[at]===' '||text[at]==='\n'||text[at]==='\r'||text[at]==='\t'))at++;};
    const token=expected=>{ws();if(part(text,at,at+expected.length)!==expected)throw 0;at+=expected.length;};
    const string=()=>{
      ws();const start=at;if(text[at++]!=='"')throw 0;
      while(at<text.length){
        const c=text[at++];
        if(c==='"')return [start,at];
        if(apply(charCode,c,[0])<32)throw 0;
        if(c==='\\'){
          const e=text[at++];
          if(e==='u'){
            for(let n=0;n<4;n++){
              const h=text[at++];if(!h||apply(stringIndex,'0123456789abcdefABCDEF',[h])===-1)throw 0;
            }
          }else if(!e||apply(stringIndex,'"\\/bfnrt',[e])===-1)throw 0;
        }
      }
      throw 0;
    };
    const integer=()=>{
      ws();const start=at;
      while(text[at]>='0'&&text[at]<='9')at++;
      const value=part(text,start,at);
      if(!value||value.length>3||(value.length>1&&value[0]==='0'))throw 0;
      return +value;
    };
    token('[');ws();
    if(text[at]===']')throw 0;
    while(true){
      token('{');let id=null,type=null,value=null,must=false,keys=create(null),keyCount=0;
      while(true){
        const keyToken=string(),name=parse(part(text,keyToken[0],keyToken[1]));
        if(descriptor(keys,name))throw 0;keys[name]=true;keyCount++;token(':');
        if(name==='id')id=integer();
        else if(name==='type')type=integer();
        else if(name==='val')value=string();
        else if(name==='must'){
          ws();if(part(text,at,at+4)==='true'){at+=4;must=true;}
          else if(part(text,at,at+5)==='false'){at+=5;must=true;}
          else if(text[at]==='0'||text[at]==='1'){at++;must=true;}
          else throw 0;
        }else throw 0;
        ws();if(text[at]===','){at++;continue;}token('}');break;
      }
      if(keyCount!==4||id===null||type===null||!value||!must||descriptor(ids,''+id))throw 0;
      let expected=false;
      for(let i=0;i<fields.length;i++)if(fields[i][0]===id&&fields[i][1]===type)expected=true;
      if(!expected)throw 0;ids[''+id]=true;count++;
      if(id===8){if(parse(part(text,value[0],value[1]))!==role)throw 0;found=true;}
      ws();if(text[at]===','){at++;continue;}token(']');break;
    }
    ws();return at===text.length&&count===fields.length&&found;
  };
  const isFinal=body=>{
    try {const p=parameters(body);return typeof p.cmd==='string'&&decoded(p.cmd)==='addWafCk_addSubmit';}
    catch(_){return true;}
  };
  const classify=body=>{
    const p=parameters(body),expected=['cmd','formId','submitContentList','vCodeId','validateCode','tmpFileList','submitOrigin','phoneValidateCodes'];
    const keys=ownKeys(p);
    if(keys.length!==expected.length)throw 0;
    for(let i=0;i<expected.length;i++)if(!descriptor(p,expected[i]))throw 0;
    if(decoded(p.cmd)!=='addWafCk_addSubmit'||decoded(p.formId)!=='6'||decoded(p.vCodeId)!=='15676'||!roleMatches(p.submitContentList))throw 0;
  };
  const pin=(target,name,value)=>define(target,name,{value,writable:false,configurable:false,enumerable:descriptor(target,name)?.enumerable===true});
  pin(proto,'open',function(method,url,...args){
    if(armed&&capture)bad();
    let state=null;
    try{
      if(typeof method!=='string'||(args.length>0&&args[0]!==true)||
          (args.length>1&&args[1]!==undefined&&args[1]!==null)||
          (args.length>2&&args[2]!==undefined&&args[2]!==null))throw 0;
      state={method:lowercase(method),url:urlOf(url),contentType:null};
    }catch(_){bad();return;}
    const argv=[method,url];for(let i=0;i<args.length;i++)argv[i+2]=args[i];
    const result=apply(nativeOpen,this,argv);
    apply(mapSet,maps,[this,state]);return result;
  });
  pin(proto,'setRequestHeader',function(name,value){
    const state=apply(mapGet,maps,[this]);
    if(typeof name!=='string'){bad();}
    else if(lowercase(name)==='content-type'){
      if(!state||state.contentType!==null||typeof value!=='string')bad();
      else state.contentType=value;
    }
    if(armed&&capture)bad();
    return apply(nativeHeader,this,[name,value]);
  });
  pin(proto,'send',function(body){
    if(invalid)return;
    const state=apply(mapGet,maps,[this]);
    const target=state&&state.method==='post'&&state.url===finalUrl;
    if(!armed){
      if(target){
        if(isFinal(body)){bad();return;}
        preflightActive++;
        const xhr=this;
        const complete=event=>{if(event.isTrusted===true){preflightActive--;apply(unlisten,xhr,['loadend',complete]);}};
        apply(listen,xhr,['loadend',complete]);
      }
      return apply(nativeSend,this,[body]);
    }
    if(invalid||capture||!target||!mime(state.contentType)){bad();return;}
    try{
      classify(body);
      const epoch=globalThis[epochKey].seal();
      if(epoch.invalid!==false||epoch.sealed!==true)throw 0;
      capture={nonce,epoch:epoch.epoch};
      // The exact immutable input string is passed once to the captured native
      // send. Neither the string nor decoded protected fields are retained.
      return apply(nativeSend,this,[body]);
    }catch(_){bad();}
  });
  pin(proto,'abort',function(...args){if(armed)bad();return apply(nativeAbort,this,args);});
  // Worker XHRs bypass main-realm prototype wrappers. Fresh ownership means
  // no worker may predate installation; constructor aliases are sealed too.
  for(const name of ['Worker','SharedWorker']){
    const Constructor=globalThis[name];
    if(typeof Constructor!=='function')continue;
    const workerPrototype=Constructor.prototype;
    const BlockedWorker=function(){bad();throw new NativeError('retained_xhr_worker_unsupported');};
    define(BlockedWorker,'prototype',{value:workerPrototype,writable:false});
    pin(workerPrototype,'constructor',BlockedWorker);
    pin(globalThis,name,BlockedWorker);
  }
  const nativeFetch=globalThis.fetch;
  pin(globalThis,'fetch',function(input,...args){
    let final=false;
    try{final=(typeof input==='string'?urlOf(input):apply(requestUrl,input,[]))===finalUrl;}
    catch(_){bad();final=true;}
    if(armed||final){bad();return apply(promiseReject,NativePromise,[new NativeError('retained_xhr_refused')]);}
    const argv=[input];for(let i=0;i<args.length;i++)argv[i+1]=args[i];
    return apply(nativeFetch,this,argv);
  });
  const beacon=Navigator.prototype.sendBeacon;
  pin(Navigator.prototype,'sendBeacon',function(url,data){
    let final=true;try{final=urlOf(url)===finalUrl;}catch(_){bad();}
    if(armed||final){bad();return false;}
    return apply(beacon,this,[url,data]);
  });
  const formAction=descriptor(HTMLFormElement.prototype,'action').get;
  const denyForm=form=>{try{if(armed||urlOf(apply(formAction,form,[]))===finalUrl){bad();return true;}}catch(_){bad();return true;}return false;};
  for(const name of ['submit','requestSubmit']){
    const native=HTMLFormElement.prototype[name];
    pin(HTMLFormElement.prototype,name,function(...args){if(denyForm(this))return;return apply(native,this,args);});
  }
  const prevent=Event.prototype.preventDefault,target=descriptor(Event.prototype,'target').get;
  apply(listen,globalThis,['submit',event=>{if(denyForm(apply(target,event,[])))apply(prevent,event,[]);},true]);
  const arm=()=>{
    if(armed||invalid||capture||preflightActive!==0){bad();return false;}
    armed=true;return true;
  };
  const snapshot=()=>{
    const result=create(null);
    result.format='retained-xhr-classifier-v1';result.nonce=capture?capture.nonce:null;
    result.state=invalid?'INVALID':capture?'CAPTURED':armed?'ARMED':'UNARMED';
    result.change_epoch=capture?capture.epoch:null;return result;
  };
  define(globalThis,key,{value:freeze({arm,snapshot}),writable:false,configurable:false});
}
"""


class RequestClassifierConflict(RuntimeError):
    def __init__(self):super().__init__('retained_xhr_classifier_conflict')


class RetainedXHRClassifier(PreDocumentChangeEpoch):
    """Internal research observer, not a transport or human authorization gate.

    Install before all page scripts in a fresh owned context. Call arm only
    after public bootstrap finishes and the separate network guard is sealed.
    A route owner parks the native Request; classify is called outside route
    callbacks. No page-supplied dict can substitute for that exact Request.
    """
    def __init__(self,context):
        require_private_transport_environment()
        self.capture_key='__jae_xhr_'+secrets.token_hex(16)
        self.capture_nonce=secrets.token_hex(32)
        self.armed=False
        self._inflight={}
        self._attempts=[]
        self._retained=None
        super().__init__(context)
        context.on('request',self._request_started)
        context.on('requestfinished',self._request_finished)
        context.on('requestfailed',self._request_failed)

    def _init_script(self):
        options={'key':self.capture_key,'epochKey':self.key,'nonce':self.capture_nonce,
                 'finalUrl':FINAL_URL,'documentUrl':CONTRACT_URL,'role':CONTRACT_ROLE,
                 'fields':[[int(f.field_id),int(f.data_type)] for f in FIELDS if f.field_id.isdigit()]}
        # A single ordered initializer, never competing add_init_script calls.
        return '('+SCRIPT+')('+json.dumps(options)+');'+super()._init_script()

    def _page_created(self,page):
        super()._page_created(page)
        if self.invalid:return
        page.on('framenavigated',lambda _:self._invalidate() if self.armed else None)
        page.on('worker',self._invalidate)

    def _request_started(self,request):
        if self.invalid:return
        if self.armed:
            self._attempts.append(request)
            if len(self._attempts)>1:self._invalidate()
        else:
            if len(self._inflight)>=256:
                self._invalidate();return
            self._inflight[id(request)]=request

    def _request_finished(self,request):
        self._inflight.pop(id(request),None)

    def _request_failed(self,request):
        self._request_finished(request)
        if self.armed:self._invalidate()

    def _refuse(self):
        self._invalidate()
        raise RequestClassifierConflict() from None

    def arm(self):
        try:
            if self.invalid or self.armed or self.baseline is not None or self._inflight:self._refuse()
            validate_snapshot(self._read('snapshot'),sealed=False)
            self.armed=True
            if self.page.evaluate('key=>globalThis[key].arm()',self.capture_key) is not True:self._refuse()
            if self.invalid or self._attempts:self._refuse()
        except Exception:self._refuse()

    def classify(self,request):
        try:
            if (not self.armed or self.invalid or self._retained is not None or len(self._attempts)!=1 or self._attempts[0] is not request
                    or request.method!='POST' or request.url!=FINAL_URL or request.resource_type!='xhr'
                    or request.is_navigation_request() or request.redirected_from is not None
                    or request.frame is not self.page.main_frame):self._refuse()
            content_type=request.header_value('content-type')
            if type(content_type) is not str or content_type.lower() not in {
                    'application/x-www-form-urlencoded','application/x-www-form-urlencoded; charset=utf-8',
                    'application/x-www-form-urlencoded;charset=utf-8'}:self._refuse()
            value=self.page.evaluate('key=>globalThis[key].snapshot()',self.capture_key)
            if (type(value) is not dict or set(value)!={'format','nonce','state','change_epoch'}
                    or value['format']!='retained-xhr-classifier-v1' or value['state']!='CAPTURED'
                    or value['nonce']!=self.capture_nonce or type(value['change_epoch']) is not int):self._refuse()
            self.baseline=validate_snapshot(self._read('snapshot'),sealed=True)
            if self.baseline!=value['change_epoch'] or self.invalid:self._refuse()
            self._retained=request
            return {'contract':CONTRACT_VERSION,'command':'addWafCk_addSubmit','form_id':6,
                    'role':CONTRACT_ROLE,'category':'final_application','change_epoch':self.baseline}
        except Exception:self._refuse()

    def require_exact(self,request):
        try:
            if self._retained is not request or len(self._attempts)!=1 or self._attempts[0] is not request or self.invalid:self._refuse()
            current=super().unchanged()
            value=self.page.evaluate('key=>globalThis[key].snapshot()',self.capture_key)
            if (type(value) is not dict or value!={'format':'retained-xhr-classifier-v1','nonce':self.capture_nonce,
                    'state':'CAPTURED','change_epoch':current} or self.invalid):self._refuse()
            return current
        except Exception:self._refuse()
