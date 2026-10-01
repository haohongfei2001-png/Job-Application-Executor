"""Read-only identity of the dedicated launched browser and its own driver.

No arbitrary PID adoption, signaling, default-profile discovery or reconnect.
Snapshots are captured around native CDP process inventory before any user data.
Absence/reuse is conservative; command drift alone never proves a process died.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass,field

from .qiyunfang import digest
from ..autonomy.profile_editor import _DirectoryPin
from ..autonomy.task_preparation import _LocalFile

HEX=re.compile(r'[a-f0-9]{64}\Z')


class ProcessIdentityUnknown(RuntimeError):
    def __init__(self):super().__init__('preparation_process_identity_unknown')


def _snapshot(pid):
    if type(pid) is not int or not 1<pid<=2147483647:raise ProcessIdentityUnknown()
    try:
        # TZ changes only this read-only formatter, never system/user settings.
        result=subprocess.run(['ps','-ww','-p',str(pid),'-o','uid=','-o','ppid=','-o','lstart=','-o','stat=','-o','command='],
            capture_output=True,text=True,timeout=3,env={**os.environ,'TZ':'UTC','LC_ALL':'C'})
    except (OSError,subprocess.SubprocessError):raise ProcessIdentityUnknown() from None
    if result.returncode==1 and not result.stdout.strip() and not result.stderr.strip():return None
    if result.returncode!=0 or result.stderr.strip():raise ProcessIdentityUnknown()
    # lstart is weekday month day HH:MM:SS year; command is never persisted.
    parts=result.stdout.strip().split(None,8)
    if (len(parts)!=9 or not parts[0].isdigit() or not parts[1].isdigit()
            or not parts[7] or not parts[8]):raise ProcessIdentityUnknown()
    uid=int(parts[0]);ppid=int(parts[1]);start=' '.join(parts[2:7]);status=parts[7]
    if not re.fullmatch(r'\w{3} \w{3} \d{1,2} \d{2}:\d{2}:\d{2} \d{4}',start):raise ProcessIdentityUnknown()
    return {'pid':pid,'ppid':ppid,'uid':uid,'start_sha':hashlib.sha256(start.encode()).hexdigest(),
            'command_sha':hashlib.sha256(parts[8].encode()).hexdigest(),'zombie':status.startswith('Z')}


def _browser_pid(browser):
    cdp=None
    try:
        cdp=browser.new_browser_cdp_session()
        items=cdp.send('SystemInfo.getProcessInfo')['processInfo']
        selected=[entry['id'] for entry in items if entry.get('type')=='browser']
        if len(selected)!=1 or type(selected[0]) is not int:raise ProcessIdentityUnknown()
        return selected[0]
    except Exception:raise ProcessIdentityUnknown() from None
    finally:
        if cdp is not None:
            try:cdp.detach()
            except Exception:pass


def _validate(record):
    if not isinstance(record,dict) or set(record)!={'browser','driver'}:raise ProcessIdentityUnknown()
    for part in record.values():
        if (not isinstance(part,dict) or set(part)!={'pid','ppid','uid','start_sha','command_sha','zombie'}
                or type(part['pid']) is not int or not 1<part['pid']<=2147483647
                or type(part['ppid']) is not int or part['ppid']<1
                or type(part['uid']) is not int or part['uid']!=os.geteuid() or part['zombie'] is not False
                or any(not isinstance(part[key],str) or not HEX.fullmatch(part[key]) for key in ('start_sha','command_sha'))):
            raise ProcessIdentityUnknown()
    if (record['browser']['pid']==record['driver']['pid']
            or record['browser']['ppid']!=record['driver']['pid']):raise ProcessIdentityUnknown()
    return copy.deepcopy(record)


@dataclass(frozen=True,repr=False)
class OwnedProcessIdentity:
    record:dict=field(repr=False)

    @classmethod
    def capture(cls,pw,browser):
        try:driver_pid=pw._impl_obj._connection._transport._proc.pid
        except Exception:raise ProcessIdentityUnknown() from None
        pid=_browser_pid(browser)
        before={'browser':_snapshot(pid),'driver':_snapshot(driver_pid)}
        _validate(before)
        if before['driver']['ppid']!=os.getpid():raise ProcessIdentityUnknown()
        if _browser_pid(browser)!=pid:raise ProcessIdentityUnknown()
        after={'browser':_snapshot(pid),'driver':_snapshot(driver_pid)}
        if before!=after:raise ProcessIdentityUnknown()
        return cls(_validate(after))

    @property
    def process_sha(self):return digest(_validate(self.record))

    def save(self,root):
        """Publish an immutable value-free receipt before field admission."""
        record=_validate(self.record);sha=digest(record)
        payload=json.dumps({'version':1,'identity':record},sort_keys=True,separators=(',',':')).encode('ascii')
        name='preparation-process-'+sha+'.json'
        with _DirectoryPin(root) as pin:
            pin.fence()
            try:fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=pin.descriptor)
            except FileExistsError:
                if self.load(root,sha).record!=record:raise ProcessIdentityUnknown()
                return sha
            try:
                os.fchmod(fd,0o600)
                if os.write(fd,payload)!=len(payload):raise ProcessIdentityUnknown()
                os.fsync(fd)
                saved=os.fstat(fd);entry=os.stat(name,dir_fd=pin.descriptor,follow_symlinks=False)
                if (saved.st_dev,saved.st_ino)!=(entry.st_dev,entry.st_ino):raise ProcessIdentityUnknown()
                pin.fence();os.fsync(pin.descriptor);pin.fence()
            finally:os.close(fd)
        return sha

    @classmethod
    def load(cls,root,expected_sha):
        if not isinstance(expected_sha,str) or not HEX.fullmatch(expected_sha):raise ProcessIdentityUnknown()
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ProcessIdentityUnknown()
                result[key]=value
            return result
        try:
            with _LocalFile(str(root/('preparation-process-'+expected_sha+'.json')),4096,private=True) as file:
                payload=json.loads(file.read(),object_pairs_hook=unique);file.fence()
            if not isinstance(payload,dict) or set(payload)!={'version','identity'} or type(payload['version']) is not int or payload['version']!=1:
                raise ProcessIdentityUnknown()
            record=_validate(payload['identity'])
            if digest(record)!=expected_sha:raise ProcessIdentityUnknown()
            return cls(record)
        except Exception:raise ProcessIdentityUnknown() from None

    def verify_os(self):
        """OS-only live check for synchronous request callbacks; no CDP RPC.

        The owner must establish native browser/document binding before arming
        and recheck it after the native primitive. This method preserves only
        the previously captured process identities, not a current DOM snapshot.
        """
        record=_validate(self.record)
        for expected in record.values():
            if _snapshot(expected['pid'])!=expected:raise ProcessIdentityUnknown()
        return digest(record)

    def verify(self,browser):
        record=_validate(self.record)
        if _browser_pid(browser)!=record['browser']['pid']:raise ProcessIdentityUnknown()
        for name,expected in record.items():
            if _snapshot(expected['pid'])!=expected:raise ProcessIdentityUnknown()
        # Native inventory after OS reads prevents a dead/replaced pipe from
        # certifying a newly reused PID as the original owned browser.
        if _browser_pid(browser)!=record['browser']['pid']:raise ProcessIdentityUnknown()
        return digest(record)

    def absence(self):
        """Read-only process epoch proof, only for a previously owned receipt.

        Any present-but-different snapshot is UNKNOWN, never an absence shortcut.
        Formatted start-time drift is not trusted as proof of PID reuse.
        This does not authorize reopening a context or retrying a final request.
        """
        try:
            record=_validate(self.record);all_absent=True
            for expected in record.values():
                current=_snapshot(expected['pid'])
                if current is None or current['zombie']:continue
                if current!=expected:return {'status':'UNKNOWN'}
                all_absent=False
            return {'status':'ABSENT' if all_absent else 'PRESENT','process_sha':digest(record)}
        except Exception:return {'status':'UNKNOWN'}
