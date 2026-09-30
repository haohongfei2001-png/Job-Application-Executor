"""Value-free local preparation. This module has no website execution authority."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import parse_qsl

from .profile_setup import PROFILE_LIMIT, _profile_bytes

RESUME_LIMIT = 20 * 1024 * 1024
RESUME_EXTENSIONS = {"resume_pdf": ".pdf", "resume_docx": ".docx", "resume_doc": ".doc"}
MAX_REVISION = 9007199254740991
CONTRACT_ID = "qiyunfang-wuhan-implementation-v1"
CONTRACT_URL = "https://www.qiyunfang.com/h-col-124.html"
CONTRACT_COMPANY = "武汉启云方科技有限公司"
CONTRACT_COMPANIES = frozenset({CONTRACT_COMPANY, "启云方"})
CONTRACT_ROLE = "应用实施工程师（武汉）"
CONTRACT_OBSERVED_AT = "2026-09-30"

# Cached public-page labels only. A label does not establish requiredness,
# field semantics, current availability, or a website persistence certificate.
CHECKLIST_FIELDS = (
    ("identity.full_name", "姓名"),
    ("identity.phone", "电话号码"),
    ("identity.email", "邮箱号码"),
    ("identity.gender", "性别"),
    ("education.highest.degree", "最高学历"),
    ("education.highest.school", "毕业院校"),
    ("education.highest.college", "学院"),
    ("education.highest.major", "专业"),
    ("education.highest.graduation_date", "毕业时间"),
    ("language.cet6.level", "英语证书情况（本地六级记录）"),
    ("language.cet6.score", "英语考级分数（本地六级记录）"),
    ("preferences.preferred_cities", "期望工作城市"),
)
MANUAL_STEPS = (
    "页面信息来自缓存观察；字段是否必填尚未核实，请本人在网站检查。",
    "毕业年份选项曾显示 2026、2027、2028；请本人核对当前选项和自己的资料。",
    "英语证书选项曾显示 CET4、CET6、IELTS、TOEFL、其他；本地六级资料不代表已选择网站选项。",
    "请本人核对投递岗位1；投递岗位2是否需要填写尚未核实，不会自动选择。",
    "身份证号码、验证码、隐私保护协议须由本人在网站处理。",
    "附件简历须由本人上传并确认；本地版本匹配不代表网站已上传或保存。",
    "最终提交须由本人完成；本地清单不证明账号、网站草稿或提交状态。",
)


def _validate_request(task_id, expected_revision):
    if (not isinstance(task_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", task_id)
            or type(expected_revision) is not int
            or not 0 <= expected_revision <= MAX_REVISION):
        raise ValueError("invalid preparation request")


def parse_preparation_query(query):
    """Require one bounded task and revision; reject ambiguous query envelopes."""
    if not isinstance(query, str) or len(query) > 2048:
        raise ValueError("invalid preparation request")
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                      encoding="utf-8", errors="strict", max_num_fields=2)
    if len(pairs) != 2 or {key for key, _ in pairs} != {"task_id", "expected_revision"}:
        raise ValueError("invalid preparation request")
    values = dict(pairs)
    revision = values["expected_revision"]
    if not re.fullmatch(r"0|[1-9][0-9]{0,15}", revision):
        raise ValueError("invalid preparation request")
    task_id, revision = values["task_id"], int(revision)
    _validate_request(task_id, revision)
    return task_id, revision


class _Unavailable(Exception):
    """Admission failure with no path or file details in the response."""


def _signature(info):
    if info is None:
        return None
    identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
    # Changes to unrelated directory entries need not invalidate a file read.
    if stat.S_ISDIR(info.st_mode):
        return identity
    return identity + (info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _entry(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


class _LocalFile:
    """Retain a no-follow descriptor chain and fence every observed path edge.

    Rejected entries are never read, chmodded, repaired, or resolved through an
    alias. Their entry observations are still fenced before returning a result.
    """

    def __init__(self, path, limit, *, private):
        self.path, self.limit, self.private = path, limit, private
        self.descriptors = []
        self.edges = []
        self.descriptor = None
        self.info = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)

    def _admit(self):
        if not isinstance(self.path, str) or not self.path or "\x00" in self.path:
            raise _Unavailable()
        path = Path(self.path)
        if (not path.is_absolute() or str(path) != self.path
                or ".." in path.parts or path.anchor != "/" or not path.name):
            raise _Unavailable()
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent = os.open("/", flags | os.O_DIRECTORY)
        self.descriptors.append(parent)
        for index, name in enumerate(path.parts[1:]):
            info = _entry(parent, name)
            self.edges.append((parent, name, _signature(info)))
            leaf = index == len(path.parts) - 2
            if info is None:
                raise _Unavailable()
            if not leaf:
                if not stat.S_ISDIR(info.st_mode):
                    raise _Unavailable()
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=parent)
                self.descriptors.append(child)
                if _signature(os.fstat(child)) != _signature(info):
                    raise RuntimeError("preparation changed")
                parent = child
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or not 0 <= info.st_size <= self.limit
                    or (self.private and stat.S_IMODE(info.st_mode) & 0o077)):
                raise _Unavailable()
            descriptor = os.open(name, flags | os.O_NONBLOCK, dir_fd=parent)
            self.descriptors.append(descriptor)
            self.descriptor, self.info = descriptor, info
            if _signature(os.fstat(descriptor)) != _signature(info):
                raise RuntimeError("preparation changed")

    def read(self):
        try:
            self._admit()
            chunks, size = [], 0
            while size <= self.limit:
                chunk = os.read(self.descriptor, min(65536, self.limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            self.fence()
            if size != self.info.st_size or size > self.limit:
                raise RuntimeError("preparation changed")
            return b"".join(chunks)
        except OSError:
            # A failed read still fences every path edge admitted so far.
            self.fence()
            raise _Unavailable() from None

    def fence(self):
        try:
            for parent, name, signature in self.edges:
                if _signature(_entry(parent, name)) != signature:
                    raise RuntimeError("preparation changed")
            if self.descriptor is not None and _signature(os.fstat(self.descriptor)) != _signature(self.info):
                raise RuntimeError("preparation changed")
        except OSError:
            raise RuntimeError("preparation changed") from None


def _field_value(profile, key):
    if "fields" in profile:
        return profile["fields"].get(key, {}).get("value")
    current = profile
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _item_status(profile, key):
    if profile is None:
        return "needs_review"
    value = _field_value(profile, key)
    if value is None or value == "" or value == []:
        return "missing"
    if isinstance(value, str):
        return "recorded_locally" if value.strip() else "missing"
    if key == "language.cet6.score" and (type(value) is int or (type(value) is float and math.isfinite(value))):
        return "recorded_locally"
    if (key == "preferences.preferred_cities" and isinstance(value, list)
            and value and all(isinstance(item, str) and item.strip() for item in value)):
        return "recorded_locally"
    return "needs_review"


def _contract(spec):
    matched = (spec.get("target_url") == CONTRACT_URL
               and spec.get("company") in CONTRACT_COMPANIES
               and spec.get("role") == CONTRACT_ROLE)
    return {"matched": matched, "id": CONTRACT_ID if matched else None,
            "source_url": CONTRACT_URL if matched else None,
            "observed_at": CONTRACT_OBSERVED_AT if matched else None,
            "coverage": "observed_fields_only" if matched else "unavailable",
            "requiredness": "UNVERIFIED",
            "freshness": "cached_observation" if matched else "unavailable"}


def prepare_task(queue, task_id, expected_revision):
    """Observe the persisted task's profile, without consulting current settings."""
    _validate_request(task_id, expected_revision)
    try:
        before = queue.get(task_id)
    except KeyError:
        raise ValueError("invalid preparation request") from None
    if (before["revision"] != expected_revision or before["stage"] == "CANCELLED"
            or before.get("run_state") == "CANCELLED"):
        raise RuntimeError("preparation changed")
    spec = before["spec"]
    contract = _contract(spec)
    profile, version = None, None
    resume = {"status": "unavailable", "version": None}
    with ExitStack() as stack:
        profile_file = stack.enter_context(_LocalFile(spec.get("profile_ref"), PROFILE_LIMIT, private=True))
        resume_file = None
        try:
            raw = profile_file.read()
            # Reuse strict import validation, but hash the exact original bytes,
            # not the validator's normalized serialization or model defaults.
            text = raw.decode("utf-8")
            _profile_bytes(text)
            profile = json.loads(text)
            version = hashlib.sha256(raw).hexdigest()
        except (_Unavailable, ValueError, TypeError, UnicodeError, RecursionError):
            pass
        if profile is not None:
            asset = profile.get("assets", {}).get("resume")
            if asset is None:
                resume = {"status": "missing", "version": None}
            elif (asset.get("kind") in RESUME_EXTENSIONS
                  and isinstance(asset.get("path"), str)
                  and Path(asset["path"]).suffix.lower() == RESUME_EXTENSIONS[asset["kind"]]):
                # A designated supported resume only. Never even probe other
                # imported paths, source documents, or credential/config files.
                resume_file = stack.enter_context(_LocalFile(asset.get("path"), RESUME_LIMIT, private=False))
                try:
                    digest = hashlib.sha256(resume_file.read()).hexdigest()
                    stored = asset.get("sha256")
                    status = "unverified"
                    if isinstance(stored, str) and re.fullmatch(r"[0-9a-fA-F]{64}", stored):
                        status = "local_version_matches" if digest == stored.lower() else "local_version_differs"
                    resume = {"status": status, "version": digest}
                except _Unavailable:
                    pass
        items = [{"key": key, "label": label, "status": _item_status(profile, key),
                  "note": "仅说明本地记录情况；内容、网站选项和必填要求仍需本人核对。"}
                 for key, label in CHECKLIST_FIELDS] if contract["matched"] else []
        result = {"task_id": task_id, "task_revision": expected_revision,
                  "mode": "LOCAL_PREPARATION_ONLY",
                  "profile": {"status": "available" if profile is not None else "unavailable", "version": version},
                  "contract": contract, "items": items, "resume": resume,
                  "manual_steps": list(MANUAL_STEPS) if contract["matched"] else [
                      "没有可用于此任务的已核对表单清单；仅检查本地资料和简历版本。",
                      "本地记录不证明网站上传、保存、账号或提交状态；请本人在网站核对并完成操作。"],
                  "capabilities": {"live_write": False, "submit": False,
                                   "account_verified": False, "server_draft_verified": False}}
        profile_file.fence()
        if resume_file is not None:
            resume_file.fence()
        try:
            after = queue.get(task_id)
        except KeyError:
            raise RuntimeError("preparation changed") from None
        if after != before:
            raise RuntimeError("preparation changed")
        profile_file.fence()
        if resume_file is not None:
            resume_file.fence()
        return result
