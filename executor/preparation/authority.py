"""One-shot, task/session/content-bound preparation authority and durable fence.

Not wired to HTTP or the model. The session owner must supply a verified fresh
browser binding, check service/session liveness on every guard, and independently
prove context closure before releasing the global browser-work fence.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time
from contextlib import ExitStack, contextmanager

from ..autonomy.profile_setup import PROFILE_LIMIT, validate_profile_text
from ..autonomy.profile_editor import _DirectoryPin
from ..autonomy.task_preparation import (_LocalFile, _Unavailable, RESUME_LIMIT,
                                        prepare_task)
from ..autonomy.queue import STOPPED, PREPARATION_MARKER
from .qiyunfang import CONTRACT_VERSION, digest, map_routine_fields, select_plan

HEX = re.compile(r"[a-f0-9]{64}\Z")
SESSION = re.compile(r"[A-Za-z0-9_-]{24,512}\Z")
TTL = 120
MAX_PENDING = 8


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class PreparationConflict(RuntimeError):
    def __init__(self): super().__init__("preparation_authority_conflict")


@contextmanager
def _admission_guard(root):
    """Serialize activation/migration BEFORE SQLite; never acquire in reverse.

    Activation holds the same migration inode through its entire transaction.
    Retaining the root chain prevents a replaced authority path from publishing
    outside it. Contention refuses promptly and does not consume the offer.
    """
    with _DirectoryPin(root) as pin:
        pin.fence()
        fd = os.open("migration.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=pin.descriptor)
        try:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()):
                raise PreparationConflict()
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise PreparationConflict() from None
            current = os.stat("migration.lock", dir_fd=pin.descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise PreparationConflict()
            pin.fence()
            yield
            pin.fence()
        finally: os.close(fd)


@contextmanager
def _material(queue, task_id, revision):
    """Retain no-follow file authority through admission; never persist values."""
    report = prepare_task(queue, task_id, revision)
    if not report["contract"]["matched"] or report["profile"]["status"] != "available":
        raise PreparationConflict()
    before = queue.get(task_id)
    if (before["revision"] != revision or before["stage"] in STOPPED
            or before["spec"].get("attachment_refs")):
        raise PreparationConflict()
    with ExitStack() as stack:
        source = stack.enter_context(_LocalFile(before["spec"].get("profile_ref"), PROFILE_LIMIT, private=True))
        try:
            raw = source.read()
            profile = validate_profile_text(raw.decode("utf-8"))
        except (_Unavailable, ValueError, UnicodeError, RecursionError):
            raise PreparationConflict() from None
        if hashlib.sha256(raw).hexdigest() != report["profile"]["version"]:
            raise PreparationConflict()
        files = [source]
        asset = profile.get("assets", {}).get("resume")
        if asset is not None and report["resume"]["version"] is not None:
            resume = stack.enter_context(_LocalFile(asset.get("path"), RESUME_LIMIT, private=False))
            try: data = resume.read()
            except _Unavailable: raise PreparationConflict() from None
            if hashlib.sha256(data).hexdigest() != report["resume"]["version"]:
                raise PreparationConflict()
            files.append(resume)
        def fence():
            for item in files: item.fence()
        fence()
        if queue.get(task_id) != before:
            raise PreparationConflict()
        yield before, report, profile, fence
        fence()


def _retire_resume_requests(db, nonce_sha, now):
    db.execute("UPDATE preparation_resume_stages SET outcome='UNKNOWN_OUTCOME',updated=? WHERE upload_intent_sha IN (SELECT intent_sha FROM preparation_resume_uploads WHERE preparation_nonce_sha=?) AND outcome='ATTEMPTED'",(now,nonce_sha))
    db.execute("UPDATE preparation_resume_uploads SET outcome='UNKNOWN_OUTCOME',updated=? WHERE preparation_nonce_sha=? AND outcome='ATTEMPTED'",(now,nonce_sha))


def _marker_bytes(nonce_sha, binding):
    return json.dumps({"nonce_sha": nonce_sha, "browser": binding},
                      sort_keys=True, separators=(",", ":")).encode("ascii")


def _read_marker(root):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise PreparationConflict()
            result[key] = value
        return result
    with _LocalFile(str(root / PREPARATION_MARKER), 2048, private=True) as marker:
        data = json.loads(marker.read(), object_pairs_hook=unique)
        if (not isinstance(data, dict) or set(data) != {"nonce_sha", "browser"}
                or not isinstance(data["nonce_sha"], str) or not HEX.fullmatch(data["nonce_sha"])):
            raise PreparationConflict()
        PreparationAuthority._browser(data["browser"])
        marker.fence()
        return data


def _create_marker(root, nonce_sha, binding):
    # Retain every no-follow directory edge BEFORE any filesystem effect.
    # A marker/SQLite publication is not one atomic operation: uncertainty
    # leaves the marker fenced for read-only reconciliation, never auto-retry.
    with _DirectoryPin(root) as pin:
        pin.fence()
        fd = os.open(PREPARATION_MARKER,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=pin.descriptor)
        try:
            os.fchmod(fd, 0o600)
            data = _marker_bytes(nonce_sha, binding)
            if os.write(fd, data) != len(data): raise PreparationConflict()
            os.fsync(fd)
            info = os.fstat(fd)
            entry = os.stat(PREPARATION_MARKER, dir_fd=pin.descriptor, follow_symlinks=False)
            if (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino):
                raise PreparationConflict()
            pin.fence()
            os.fsync(pin.descriptor)
            pin.fence()
        finally: os.close(fd)


def _blocking_sentinel(parent):
    try:
        fd = os.open(PREPARATION_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
    except FileExistsError:
        return
    try: os.fchmod(fd, 0o600); os.write(fd, b"UNRECOGNIZED_MARKER\n"); os.fsync(fd)
    finally: os.close(fd)
    os.fsync(parent)


def _nonce_retired(root, nonce_sha):
    # A retired receipt also fences the nonce if a later SQLite tombstone
    # commit was interrupted. No file contents or applicant data are inspected.
    with _DirectoryPin(root) as pin:
        found = any(name.startswith("preparation-closed-" + nonce_sha + "-")
                    for name in os.listdir(pin.descriptor))
        pin.fence()
        return found


def _clear_marker(root, nonce_sha, binding):
    # Portable unlink has no inode CAS. Retain the closed receipt under a new
    # unique name, verify the moved inode, and never unlink replaceable names.
    with _DirectoryPin(root) as pin, _LocalFile(str(root / PREPARATION_MARKER), 2048, private=True) as marker:
        expected = _marker_bytes(nonce_sha, binding)
        if marker.read() != expected: raise PreparationConflict()
        original = os.fstat(marker.descriptor)
        marker.fence(); pin.fence()
        retired = "preparation-closed-" + nonce_sha + "-" + secrets.token_hex(16)
        try:
            os.rename(PREPARATION_MARKER, retired, src_dir_fd=pin.descriptor, dst_dir_fd=pin.descriptor)
            moved = os.stat(retired, dir_fd=pin.descriptor, follow_symlinks=False)
            if (moved.st_dev, moved.st_ino, moved.st_mode, moved.st_uid, moved.st_nlink, moved.st_size) != (
                    original.st_dev, original.st_ino, original.st_mode, original.st_uid, original.st_nlink, original.st_size):
                raise PreparationConflict()
            if os.pread(marker.descriptor, 2048, 0) != expected:
                raise PreparationConflict()
            pin.fence(); os.fsync(pin.descriptor); pin.fence()
        except BaseException:
            # Covers inode/payload/root/fsync uncertainty alike. Preserve the
            # retired entry and restore an active refusal without overwriting.
            _blocking_sentinel(pin.descriptor)
            raise


def _primitive_fields(plan):
    return [item["field_id"] for item in plan
            for _ in (item["value"] if item["field_id"] == "17" else [None])]


class PreparationAuthority:
    def __init__(self, queue, *, session_valid, clock=time.monotonic):
        self.queue, self.session_valid, self.clock = queue, session_valid, clock
        self.pending = {}
        self.active = {}
        self.lock = threading.RLock()
        self.instance_sha = _sha(secrets.token_urlsafe(32))

    @staticmethod
    def _browser(binding):
        keys = {"process_sha", "context_sha", "document_sha", "root_sha", "controls_sha", "resources_sha"}
        if not isinstance(binding, dict) or set(binding) != keys or any(
                not isinstance(value, str) or not HEX.fullmatch(value) for value in binding.values()):
            raise PreparationConflict()
        return copy.deepcopy(binding)

    def _session(self, session):
        if not isinstance(session, str) or not SESSION.fullmatch(session) or not self.session_valid(session):
            raise PreparationConflict()

    def issue(self, task_id, revision, session, browser_binding, selected_ids):
        """Issue only for a verified empty browser/form and exact selected values.

        The private UI must display these values, recipient and early-transmission
        warning. Reading this offer is not approval and cannot mutate the website.
        """
        self._session(session)
        binding = self._browser(browser_binding)
        with self.lock, _material(self.queue, task_id, revision) as (task, report, profile, fence):
            now = self.clock()
            self.pending = {key: value for key, value in self.pending.items() if value["expires"] > now}
            if len(self.pending) >= MAX_PENDING:
                raise PreparationConflict()
            plan = select_plan(map_routine_fields(profile), selected_ids)
            scope = {"task_id": task_id, "revision": revision, "spec_sha": digest(task["spec"]),
                     "profile_sha": report["profile"]["version"], "resume": report["resume"],
                     "contract_version": CONTRACT_VERSION, "browser": binding,
                     "plan_sha": digest(plan), "session_sha": _sha(session),
                     "authority_sha": self.instance_sha}
            nonce = secrets.token_urlsafe(32)
            record = {"scope": scope, "scope_sha": digest(scope), "plan": copy.deepcopy(plan),
                      "expires": now + TTL, "task": task}
            fence()
            self.pending[_sha(nonce)] = record
            return {"nonce": nonce, "expires_in_seconds": TTL, "scope_sha": record["scope_sha"],
                    "plan": copy.deepcopy(plan), "recipient_url": task["spec"]["target_url"],
                    "profile_version":report["profile"]["version"],"resume_version":report["resume"]["version"],
                    "company": task["spec"]["company"], "role": task["spec"]["role"],
                    "warning": "网站可能在输入时接收这些已核对的常规资料；这不授权上传、证件、验证码、协议或最终提交。",
                    "submit_capability": False}

    def revoke(self, nonce, session):
        # Revocation removes only an already-issued matching-session offer.
        # Expiry, service retirement or a temporary mutation fence must not
        # preserve authority which could revive after that fence clears.
        if not isinstance(session,str) or not SESSION.fullmatch(session):raise PreparationConflict()
        with self.lock:
            record = self.pending.get(_sha(nonce)) if isinstance(nonce, str) else None
            if record and hmac.compare_digest(record["scope"]["session_sha"], _sha(session)):
                self.pending.pop(_sha(nonce), None)

    def consume(self, nonce, session, scope_sha, browser_binding, *, approve_transmission):
        """Consume once in SQLite before any field primitive, with atomic claim."""
        self._session(session)
        binding = self._browser(browser_binding)
        if (approve_transmission is not True or not isinstance(nonce, str)
                or not SESSION.fullmatch(nonce) or not isinstance(scope_sha, str) or not HEX.fullmatch(scope_sha)):
            raise PreparationConflict()
        nonce_sha = _sha(nonce)
        with self.lock, _admission_guard(self.queue.root):
            record = self.pending.get(nonce_sha)
            if (record is None or record["expires"] <= self.clock()
                    or not hmac.compare_digest(record["scope_sha"], scope_sha)
                    or not hmac.compare_digest(record["scope"]["session_sha"], _sha(session))
                    or binding != record["scope"]["browser"]):
                raise PreparationConflict()
            task_id, revision = record["scope"]["task_id"], record["scope"]["revision"]
            with _material(self.queue, task_id, revision) as (task, report, _profile, fence):
                if (task != record["task"] or report["profile"]["version"] != record["scope"]["profile_sha"]
                        or report["resume"] != record["scope"]["resume"]):
                    raise PreparationConflict()
                owner = "preparation:" + secrets.token_hex(16)
                with self.queue.tx() as db:
                    current = self.queue._view(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
                    if (current != task or current["stage"] in STOPPED
                            or os.path.lexists(self.queue.root / PREPARATION_MARKER)
                            or _nonce_retired(self.queue.root, nonce_sha)
                            or db.execute("SELECT 1 FROM preparation_nonce_tombstones WHERE nonce_sha=?", (nonce_sha,)).fetchone()
                            or db.execute("SELECT 1 FROM tasks WHERE owner IS NOT NULL AND lease_until>?", (self.queue.clock(),)).fetchone()
                            or db.execute("SELECT 1 FROM preparation_approvals WHERE context_closed=0 OR nonce_sha=?", (nonce_sha,)).fetchone()
                            or db.execute("SELECT 1 FROM preparation_approvals WHERE task_id=? AND outcome IN ('ATTEMPTED','UNKNOWN_OUTCOME')", (task_id,)).fetchone()
                            or db.execute("SELECT 1 FROM tasks WHERE blocker IN ('otp_waiting','otp_ambiguous','user_paused_from_otp_waiting','user_paused_from_otp_ambiguous')").fetchone()
                            or db.execute("SELECT 1 FROM field_actions JOIN run_attempts USING(attempt_id) WHERE run_attempts.task_id=?", (task_id,)).fetchone()
                            or db.execute("SELECT 1 FROM run_attempts r LEFT JOIN preparation_approvals p ON p.attempt_id=r.attempt_id WHERE r.outcome IN ('ATTEMPTED','UNKNOWN_OUTCOME') AND (p.nonce_sha IS NULL OR p.context_closed=0)").fetchone()
                            or db.execute("SELECT 1 FROM profile_write_barriers WHERE profile_ref=?", (task["spec"]["profile_ref"],)).fetchone()):
                        raise PreparationConflict()
                    self._session(session)
                    if record["expires"] <= self.clock(): raise PreparationConflict()
                    fence()
                    now = self.queue.clock()
                    _create_marker(self.queue.root, nonce_sha, binding)
                    db.execute("INSERT INTO preparation_approvals(nonce_sha,task_id,owner,scope_sha,plan_sha,session_sha,outcome,browser_binding,prior_stage,prior_blocker,context_closed,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)",
                        (nonce_sha, task_id, owner, record["scope_sha"], record["scope"]["plan_sha"], _sha(session),
                         "ATTEMPTED", json.dumps(binding, sort_keys=True), task["stage"], task["blocker"], now, now))
                    db.execute("UPDATE tasks SET owner=?,lease_until=?,stage='BLOCKED',blocker='anonymous_preparation_unverified',updated=? WHERE task_id=?",
                        (owner, now + 60, now, task_id))
                    self.queue._event(db, task_id, "preparation_approved", "BLOCKED")
                    fence()
                # An uncertain commit cannot recreate authority. The durable
                # nonce and context fence remain; never retry an unconfirmed save.
                self.pending.pop(nonce_sha, None)
                permit = {"nonce_sha": nonce_sha, "owner": owner, "task_id": task_id,
                        "scope_sha": record["scope_sha"], "plan": copy.deepcopy(record["plan"]),
                        "browser": binding, "session_sha": _sha(session),
                        "task_revision": self.queue.get(task_id)["revision"],
                        "profile_sha": record["scope"]["profile_sha"], "resume": record["scope"]["resume"],
                        "spec_sha": record["scope"]["spec_sha"], "authority_sha": self.instance_sha,
                        "expires": record["expires"], "plan_sha": record["scope"]["plan_sha"]}
                self.active[nonce_sha] = copy.deepcopy(permit)
                return permit

    def guard(self, permit, session, browser_binding, *, plan_sha):
        self._session(session)
        if (not isinstance(permit, dict) or self.active.get(permit.get("nonce_sha")) != permit
                or digest(permit["plan"]) != plan_sha or permit["plan_sha"] != plan_sha):
            raise PreparationConflict()
        if (self._browser(browser_binding) != permit["browser"] or _sha(session) != permit["session_sha"]
                or permit["authority_sha"] != self.instance_sha or permit["expires"] <= self.clock()):
            raise PreparationConflict()
        with self.queue.tx() as db:
            row = db.execute("SELECT * FROM preparation_approvals WHERE nonce_sha=?", (permit["nonce_sha"],)).fetchone()
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (permit["task_id"],)).fetchone()
            if (row is None or row["context_closed"] or row["outcome"] not in {"ATTEMPTED", "PREPARED_UNVERIFIED"}
                    or row["owner"] != permit["owner"] or row["scope_sha"] != permit["scope_sha"]
                    or row["plan_sha"] != plan_sha or row["session_sha"] != _sha(session)
                    or task is None or task["revision"] != permit["task_revision"]
                    or task["owner"] != permit["owner"] or task["lease_until"] <= self.queue.clock()
                    or task["stage"] != "BLOCKED" or task["blocker"] != "anonymous_preparation_unverified"):
                raise PreparationConflict()
        if digest(self.queue.get(permit["task_id"])["spec"]) != permit["spec_sha"]:
            raise PreparationConflict()
        report = prepare_task(self.queue, permit["task_id"], permit["task_revision"])
        if report["profile"]["version"] != permit["profile_sha"] or report["resume"] != permit["resume"]:
            raise PreparationConflict()

    def finish(self, permit, outcome, *, context_closed, session=None, browser_binding=None, plan_sha=None):
        """Caller may claim closed only after independent context disposal proof."""
        if (outcome not in {"PREPARED_UNVERIFIED", "UNKNOWN_OUTCOME"} or type(context_closed) is not bool
                or not isinstance(permit, dict) or self.active.get(permit.get("nonce_sha")) != permit):
            raise PreparationConflict()
        if outcome == "PREPARED_UNVERIFIED":
            try:
                self.guard(permit, session, browser_binding, plan_sha=plan_sha)
            except (PreparationConflict, RuntimeError, ValueError):
                outcome = "UNKNOWN_OUTCOME"
        with self.queue.tx() as db:
            row = db.execute("SELECT * FROM preparation_approvals WHERE nonce_sha=?", (permit["nonce_sha"],)).fetchone()
            if (row is None or row["owner"] != permit["owner"] or row["context_closed"]
                    or row["scope_sha"] != permit["scope_sha"]
                    or (row["outcome"] == "UNKNOWN_OUTCOME" and outcome != "UNKNOWN_OUTCOME")):
                raise PreparationConflict()
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (permit["task_id"],)).fetchone()
            now = self.queue.clock()
            if (outcome == "PREPARED_UNVERIFIED" and (task is None or task["revision"] != permit["task_revision"]
                    or task["owner"] != permit["owner"] or task["lease_until"] <= now
                    or not self.session_valid(session) or permit["expires"] <= self.clock())):
                outcome = "UNKNOWN_OUTCOME"
            if outcome == "PREPARED_UNVERIFIED":
                run = db.execute("SELECT * FROM run_attempts WHERE attempt_id=?", (row["attempt_id"],)).fetchone()
                actions = db.execute("SELECT field_sha256,outcome FROM field_actions WHERE attempt_id=?", (row["attempt_id"],)).fetchall()
                expected = {digest({"scope_sha": permit["scope_sha"], "field_id": key, "ordinal": index})
                            for index, key in enumerate(_primitive_fields(permit["plan"]))}
                if (run is None or run["owner"] != permit["owner"] or run["task_id"] != permit["task_id"]
                        or run["outcome"] != "RETURNED_UNVERIFIED" or len(actions) != len(expected)
                        or {action["field_sha256"] for action in actions} != expected
                        or any(action["outcome"] != "DOM_READBACK_UNVERIFIED" for action in actions)):
                    outcome = "UNKNOWN_OUTCOME"
            if outcome == "UNKNOWN_OUTCOME":
                _retire_resume_requests(db, permit["nonce_sha"], now)
            if outcome == "UNKNOWN_OUTCOME" and row["attempt_id"]:
                db.execute("UPDATE field_actions SET outcome='UNKNOWN_OUTCOME',updated=? WHERE attempt_id=?", (now, row["attempt_id"]))
                db.execute("UPDATE run_attempts SET outcome='UNKNOWN_OUTCOME',updated=? WHERE attempt_id=? AND owner=?", (now, row["attempt_id"], permit["owner"]))
            db.execute("UPDATE preparation_approvals SET outcome=?,context_closed=?,updated=? WHERE nonce_sha=?",
                       (outcome, int(context_closed), now, permit["nonce_sha"]))
            if context_closed:
                # Closure proves no future transport can run. It cannot prove
                # whether a previously consumed final request reached the site.
                db.execute("UPDATE preparation_final_requests SET outcome='UNKNOWN_OUTCOME',updated=? WHERE preparation_nonce_sha=? AND outcome='ATTEMPTED'",
                           (now, permit["nonce_sha"]))
                _clear_marker(self.queue.root, permit["nonce_sha"], permit["browser"])
                db.execute("UPDATE tasks SET owner=NULL,lease_until=NULL,updated=? WHERE task_id=? AND owner=?",
                           (now, permit["task_id"], permit["owner"]))
            # Do not overwrite user pause/cancel, emit READY, or requeue work.
        if context_closed:
            self.active.pop(permit["nonce_sha"], None)
        return {"status": outcome, "context_closed": context_closed, "submit_capability": False}

    def reconcile_closed_context(self, nonce_sha, session, observer):
        """Read-only browser reconciliation, then bookkeeping closure only.

        `observer` is an internal owned-browser inventory capability, never an
        HTTP JSON argument or user/model assertion. It must independently prove
        the exact original process/context absent; unknown/unreachable is refused.
        This method cannot mint a permit, replay a primitive, or report success.
        """
        self._session(session)
        if not isinstance(nonce_sha, str) or not HEX.fullmatch(nonce_sha) or not callable(observer):
            raise PreparationConflict()
        with self.lock:
            if nonce_sha in self.active:
                raise PreparationConflict()
            with self.queue.tx() as db:
                record = db.execute("SELECT * FROM preparation_approvals WHERE nonce_sha=?", (nonce_sha,)).fetchone()
                record = dict(record) if record else None
                marker = _read_marker(self.queue.root) if os.path.lexists(self.queue.root / PREPARATION_MARKER) else None
                if record is not None:
                    if record["context_closed"]: raise PreparationConflict()
                    binding = self._browser(json.loads(record["browser_binding"]))
                    if marker is not None and marker != {"nonce_sha": nonce_sha, "browser": binding}:
                        raise PreparationConflict()
                elif marker is not None and marker["nonce_sha"] == nonce_sha:
                    binding = self._browser(marker["browser"])
                else:
                    raise PreparationConflict()
            evidence = observer(copy.deepcopy(binding))
            if evidence != {"status": "ABSENT", "browser": binding}:
                raise PreparationConflict()
            with self.queue.tx() as db:
                self._session(session)
                current = db.execute("SELECT * FROM preparation_approvals WHERE nonce_sha=?", (nonce_sha,)).fetchone()
                current = dict(current) if current else None
                if current != record or nonce_sha in self.active:
                    raise PreparationConflict()
                present = os.path.lexists(self.queue.root / PREPARATION_MARKER)
                if present:
                    if _read_marker(self.queue.root) != {"nonce_sha": nonce_sha, "browser": binding}:
                        raise PreparationConflict()
                    _clear_marker(self.queue.root, nonce_sha, binding)
                elif marker is not None:
                    raise PreparationConflict()
                now = self.queue.clock()
                if record is not None:
                    _retire_resume_requests(db, nonce_sha, now)
                    db.execute("UPDATE preparation_approvals SET outcome='UNKNOWN_OUTCOME',context_closed=1,updated=? WHERE nonce_sha=?", (now, nonce_sha))
                    db.execute("UPDATE preparation_final_requests SET outcome='UNKNOWN_OUTCOME',updated=? WHERE preparation_nonce_sha=? AND outcome='ATTEMPTED'", (now, nonce_sha))
                    db.execute("UPDATE tasks SET owner=NULL,lease_until=NULL,updated=? WHERE task_id=? AND owner=?", (now, record["task_id"], record["owner"]))
                    if record["attempt_id"]:
                        db.execute("UPDATE field_actions SET outcome='UNKNOWN_OUTCOME',updated=? WHERE attempt_id=?", (now, record["attempt_id"]))
                        db.execute("UPDATE run_attempts SET outcome='UNKNOWN_OUTCOME',updated=? WHERE attempt_id=? AND owner=? AND task_id=?", (now, record["attempt_id"], record["owner"], record["task_id"]))
                else:
                    db.execute("INSERT INTO preparation_nonce_tombstones VALUES(?,?,'RECOVERED_ORPHAN',?)",
                               (nonce_sha, json.dumps(binding, sort_keys=True), now))
                    db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(NULL,?,'preparation_orphan_closed','BLOCKED')", (now,))
            self.pending.pop(nonce_sha, None)
            return {"status": "UNKNOWN_OUTCOME", "context_closed": True, "submit_capability": False}


class PreparationJournal:
    """Bridge one preparation into the existing value-free primitive journal.

    Only downgrade/invalidate operations may outlive a cancelled task lease.
    A historical DOM readback never becomes independent draft or READY evidence.
    """
    def __init__(self, authority, permit, session, browser_binding):
        authority.guard(permit, session, browser_binding, plan_sha=permit["plan_sha"])
        self.authority, self.permit = authority, copy.deepcopy(permit)
        self.session, self.binding = session, copy.deepcopy(browser_binding)
        self.queue = authority.queue
        # The approval owns exactly ONE durable attempt, even after a clean
        # RETURNED_UNVERIFIED. Generic run-attempt recovery cannot grant replay.
        with self.queue.tx() as db:
            record = db.execute("SELECT * FROM preparation_approvals WHERE nonce_sha=?", (permit["nonce_sha"],)).fetchone()
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (permit["task_id"],)).fetchone()
            now = self.queue.clock()
            if (record is None or record["attempt_id"] is not None or record["context_closed"]
                    or record["outcome"] != "ATTEMPTED" or record["owner"] != permit["owner"]
                    or record["scope_sha"] != permit["scope_sha"] or record["plan_sha"] != permit["plan_sha"]
                    or task is None or task["owner"] != permit["owner"] or task["revision"] != permit["task_revision"]
                    or task["lease_until"] <= now or not authority.session_valid(session)
                    or permit["expires"] <= authority.clock()):
                raise PreparationConflict()
            self.attempt_id = secrets.token_hex(16)
            db.execute("INSERT INTO run_attempts VALUES(?,?,?,?,?,?)",
                       (self.attempt_id, permit["task_id"], permit["owner"], "ATTEMPTED", now, now))
            db.execute("UPDATE preparation_approvals SET attempt_id=?,updated=? WHERE nonce_sha=?",
                       (self.attempt_id, now, permit["nonce_sha"]))
        self.ordinal = 0
        self.closed = False
        self.actions = set()
        self.expected_fields = _primitive_fields(permit["plan"])

    def before(self, field_id):
        if self.closed or self.ordinal >= len(self.expected_fields) or field_id != self.expected_fields[self.ordinal]:
            raise PreparationConflict()
        self.authority.guard(self.permit, self.session, self.binding, plan_sha=self.permit["plan_sha"])
        identity = digest({"scope_sha": self.permit["scope_sha"], "field_id": field_id, "ordinal": self.ordinal})
        action = self.queue.begin_field_action(self.attempt_id, identity)
        self.ordinal += 1
        self.actions.add(action)
        return action

    def after(self, action, outcome):
        if action not in self.actions or outcome not in {"READBACK_VERIFIED", "UNKNOWN_OUTCOME"}:
            raise PreparationConflict()
        self.queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED" if outcome == "READBACK_VERIFIED" else outcome)

    def invalidate(self):
        # Revocation cannot grant a new write and must still work when pause,
        # cancel, expiry or final-field redraw has already revoked the lease.
        with self.queue.tx() as db:
            record = db.execute("SELECT owner,scope_sha,attempt_id FROM preparation_approvals WHERE nonce_sha=?", (self.permit["nonce_sha"],)).fetchone()
            attempt = db.execute("SELECT owner,task_id FROM run_attempts WHERE attempt_id=?", (self.attempt_id,)).fetchone()
            if (record is None or record["owner"] != self.permit["owner"] or record["scope_sha"] != self.permit["scope_sha"]
                    or record["attempt_id"] != self.attempt_id
                    or attempt is None or attempt["owner"] != self.permit["owner"] or attempt["task_id"] != self.permit["task_id"]):
                raise PreparationConflict()
            db.execute("UPDATE field_actions SET outcome='UNKNOWN_OUTCOME',updated=? WHERE attempt_id=?", (self.queue.clock(), self.attempt_id))
            db.execute("UPDATE run_attempts SET outcome='UNKNOWN_OUTCOME',updated=? WHERE attempt_id=?", (self.queue.clock(), self.attempt_id))
        self.closed = True

    def complete(self):
        if self.closed or self.ordinal != len(self.expected_fields): raise PreparationConflict()
        self.authority.guard(self.permit, self.session, self.binding, plan_sha=self.permit["plan_sha"])
        self.queue.finish_run_attempt(self.attempt_id, "RETURNED_UNVERIFIED")
        self.closed = True
