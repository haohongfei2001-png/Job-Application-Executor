"""Conservative repeated-row reconciliation for a certified site driver.

The site must expose a complete independent row inventory with stable canonical
record bindings and a monotonic draft revision. Generic pages do not satisfy
this contract. Applicant values remain in memory; the journal stores digests.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol
from types import MappingProxyType


class RowReconciliationBlocked(RuntimeError):
    pass


class RowOutcomeUnknown(RowReconciliationBlocked):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class DesiredRow:
    record_id: str
    values: Mapping[str, str] = field(repr=False)

    @property
    def values_digest(self) -> str:
        return _digest(dict(self.values))


@dataclass(frozen=True)
class SiteRow:
    record_id: str
    site_row_id: str
    values_digest: str
    managed: bool


@dataclass(frozen=True)
class RowInventory:
    revision: int
    complete: bool
    rows: tuple[SiteRow, ...]
    target_sha256: str
    draft_id_digest: str


@dataclass(frozen=True)
class RowReceipt:
    revision: int
    row_count: int
    add_count: int
    delete_count: int
    reorder_count: int
    order_digest: str


@dataclass(frozen=True, repr=False)
class RowExecutionContract:
    """A certified site driver binds canonical records to one observed draft."""
    driver: RowDriver = field(repr=False)
    desired: tuple[DesiredRow, ...] = field(repr=False)
    draft_id_digest: str
    collection_key: str
    driver_version: str
    delete_ids: frozenset[str] = frozenset()


class RowDriver(Protocol):
    capabilities: frozenset[str]

    def read_rows(self) -> RowInventory: ...
    def add_row(self, row: DesiredRow) -> None: ...
    def delete_row(self, site_row_id: str) -> None: ...
    def reorder_rows(self, record_ids: tuple[str, ...]) -> None: ...


class RowActionJournal:
    """One target/draft's durable intent; no row values or site IDs on disk."""

    def __init__(self, path: str | Path, *, scope: str):
        if not scope:
            raise RowReconciliationBlocked("row journal target/draft scope missing")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS row_scope (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL
            )""")
            expected_scope = _digest(scope)
            db.execute("INSERT OR IGNORE INTO row_scope VALUES (1,?)", (expected_scope,))
            observed_scope = db.execute("SELECT digest FROM row_scope WHERE singleton=1").fetchone()
            if not observed_scope or observed_scope[0] != expected_scope:
                raise RowReconciliationBlocked("row journal target/draft scope changed")
            db.execute("""CREATE TABLE IF NOT EXISTS row_actions (
                action_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                record_key TEXT NOT NULL, target_digest TEXT NOT NULL,
                before_revision INTEGER NOT NULL,
                after_revision INTEGER,
                outcome TEXT NOT NULL CHECK(outcome IN ('ATTEMPTED','OBSERVED_SUCCESS'))
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(row_actions)")}
            if "before_inventory" not in columns:
                db.execute("ALTER TABLE row_actions ADD COLUMN before_inventory TEXT")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def pending(self) -> tuple[tuple[str, str, str, str, int], ...]:
        with self._connect() as db:
            return tuple(db.execute("""SELECT action_id,kind,record_key,target_digest,before_revision
                FROM row_actions WHERE outcome='ATTEMPTED' ORDER BY rowid"""))

    def latest_observed_revision(self) -> int | None:
        with self._connect() as db:
            return db.execute("SELECT MAX(after_revision) FROM row_actions").fetchone()[0]

    def pending_inventory(self, action_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT before_inventory FROM row_actions "
                             "WHERE action_id=? AND outcome='ATTEMPTED'", (action_id,)).fetchone()
            if row is None:
                raise RowReconciliationBlocked("row action journal changed")
            return row[0]

    def begin(self, kind: str, record_key: str, target_digest: str,
              before_revision: int, *, before_inventory: str | None = None) -> str:
        action_id = uuid.uuid4().hex
        with self._connect() as db:
            # Serialize the pending check and intent write across processes.
            # No external action is allowed between two competing begins.
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM row_actions WHERE outcome='ATTEMPTED' LIMIT 1").fetchone():
                raise RowOutcomeUnknown("prior row effect requires read-only reconciliation")
            latest = db.execute("SELECT MAX(after_revision) FROM row_actions").fetchone()[0]
            if latest is not None and before_revision < latest:
                raise RowReconciliationBlocked("row inventory is stale after a prior write")
            db.execute("""INSERT INTO row_actions
                (action_id,kind,record_key,target_digest,before_revision,after_revision,outcome,before_inventory)
                VALUES (?,?,?,?,?,NULL,'ATTEMPTED',?)""",
                       (action_id, kind, record_key, target_digest, before_revision, before_inventory))
        return action_id

    def observed(self, action_id: str, revision: int) -> None:
        with self._connect() as db:
            changed = db.execute("""UPDATE row_actions SET outcome='OBSERVED_SUCCESS',
                after_revision=?
                WHERE action_id=? AND outcome='ATTEMPTED'""", (revision, action_id)).rowcount
            if changed != 1:
                raise RowReconciliationBlocked("row action journal changed")


class RowReconciler:
    def __init__(self, driver: RowDriver, journal: RowActionJournal,
                 *, target_sha256: str, draft_id_digest: str,
                 mutation_guard: Callable[[], None] | None = None):
        if (not re.fullmatch(r"[0-9a-f]{64}", target_sha256)
                or not re.fullmatch(r"[0-9a-f]{64}", draft_id_digest)):
            raise RowReconciliationBlocked("row target or draft binding missing")
        self.driver, self.journal = driver, journal
        self.target_sha256 = target_sha256
        self.draft_id_digest = draft_id_digest
        self.mutation_guard = mutation_guard

    def _read(self) -> RowInventory:
        try:
            inventory = self.driver.read_rows()
        except Exception:
            raise RowReconciliationBlocked("row inventory unavailable") from None
        if (not isinstance(inventory, RowInventory)
                or not isinstance(inventory.rows, tuple)
                or any(not isinstance(row, SiteRow) for row in inventory.rows)
                or type(inventory.complete) is not bool or not inventory.complete
                or type(inventory.revision) is not int or inventory.revision < 0
                or inventory.target_sha256 != self.target_sha256
                or inventory.draft_id_digest != self.draft_id_digest
                or any(not isinstance(row.record_id, str)
                       or not isinstance(row.site_row_id, str)
                       or not isinstance(row.values_digest, str)
                       or not row.record_id or not row.site_row_id or
                       type(row.managed) is not bool or
                       not re.fullmatch(r"[0-9a-f]{64}", row.values_digest)
                       for row in inventory.rows)
                or len({row.record_id for row in inventory.rows}) != len(inventory.rows)
                or len({row.site_row_id for row in inventory.rows}) != len(inventory.rows)):
            raise RowReconciliationBlocked("row identity or inventory incomplete")
        return inventory

    @staticmethod
    def _postcondition(kind: str, record_key: str, target_digest: str,
                       before_revision: int, inventory: RowInventory) -> bool:
        if inventory.revision <= before_revision or any(not row.managed for row in inventory.rows):
            return False
        if kind == "reorder":
            return _digest(tuple(row.record_id for row in inventory.rows)) == target_digest
        matches = [row for row in inventory.rows if _digest(row.record_id) == record_key]
        if kind == "add":
            return len(matches) == 1 and matches[0].values_digest == target_digest
        if kind == "delete":
            return not matches
        return False

    @staticmethod
    def _canonical_rows(desired, delete_ids=frozenset()):
        # Freeze every canonical value before the first driver read or intent.
        try:
            if (type(desired) is not tuple or type(delete_ids) is not frozenset
                    or any(not isinstance(row, DesiredRow) or type(row.record_id) is not str
                           or not row.record_id or not isinstance(row.values, Mapping)
                           or any(type(k) is not str or not k or type(v) is not str
                                  for k, v in row.values.items()) for row in desired)
                    or any(type(rid) is not str or not rid for rid in delete_ids)):
                raise ValueError
            frozen = tuple(DesiredRow(row.record_id, MappingProxyType(dict(row.values)))
                           for row in desired)
            ids = tuple(row.record_id for row in frozen)
            if len(set(ids)) != len(ids) or delete_ids.intersection(ids):
                raise ValueError
            return frozen
        except Exception:
            raise RowReconciliationBlocked("canonical row identity ambiguous") from None

    @staticmethod
    def _inventory_binding(inventory):
        # Durable full-collection preimage contains only digests and booleans.
        return json.dumps([[_digest(row.record_id), _digest(row.site_row_id),
                            row.values_digest, row.managed] for row in inventory.rows],
                          separators=(",", ":"))

    @classmethod
    def _preserves_inventory(cls, kind, record_key, before_inventory, after):
        try:
            before = json.loads(before_inventory)
            if (type(before) is not list
                    or any(type(row) is not list or len(row) != 4
                           or any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
                                  for value in row[:3])
                           or row[3] is not True for row in before)
                    or len({row[0] for row in before}) != len(before)
                    or len({row[1] for row in before}) != len(before)):
                return False
            old = {row[0]: row for row in before}
            current = {row[0]: row for row in json.loads(cls._inventory_binding(after))}
            expected = set(old)
            if kind == "add":
                if record_key in expected:
                    return False
                expected.add(record_key)
            elif kind == "delete":
                if record_key not in expected:
                    return False
                expected.remove(record_key)
            elif kind != "reorder":
                return False
            return (set(current) == expected
                    and all(current[key] == old[key] for key in expected.intersection(old)))
        except Exception:
            return False

    def _settle_pending(self, inventory: RowInventory) -> None:
        for action_id, kind, record_key, target_digest, before_revision in self.journal.pending():
            if not self._postcondition(kind, record_key, target_digest,
                                       before_revision, inventory):
                raise RowOutcomeUnknown("row write outcome unknown; replay disabled")
            preimage = self.journal.pending_inventory(action_id)
            if preimage is not None and not self._preserves_inventory(kind, record_key, preimage, inventory):
                raise RowOutcomeUnknown("row write outcome unknown; replay disabled")
            self.journal.observed(action_id, inventory.revision)

    def reconcile_pending_read_only(self) -> RowInventory:
        """Resolve prior uncertain effects from inventory, without site writes."""
        inventory = self._read()
        latest = self.journal.latest_observed_revision()
        if latest is not None and inventory.revision < latest:
            raise RowReconciliationBlocked("draft row revision regressed")
        self._settle_pending(inventory)
        return inventory

    def verify_rows_read_only(self, desired: tuple[DesiredRow, ...]) -> RowInventory:
        """Reobserve the complete canonical collection before final readiness."""
        desired = self._canonical_rows(desired)
        inventory = self.reconcile_pending_read_only()
        if (tuple(row.record_id for row in inventory.rows) !=
                tuple(row.record_id for row in desired)
                or any(not row.managed or row.values_digest != expected.values_digest
                       for row, expected in zip(inventory.rows, desired))):
            raise RowReconciliationBlocked("final row inventory differs from canonical records")
        return inventory

    def _act(self, kind: str, record_id: str, target_digest: str,
             before: RowInventory, call) -> RowInventory:
        if kind not in getattr(self.driver, "capabilities", frozenset()):
            raise RowReconciliationBlocked("row action unsupported by site driver")
        if self.mutation_guard is None:
            raise RowReconciliationBlocked("row mutation guard unavailable")
        self.mutation_guard()
        preimage = self._inventory_binding(before)
        action_id = self.journal.begin(kind, _digest(record_id), target_digest,
                                       before.revision, before_inventory=preimage)
        # The lease may have been revoked while the intent was committed.
        # Leave that intent pending for read-only recovery; never call site.
        self.mutation_guard()
        try:
            call()
        except Exception:
            pass  # The side effect may have happened before the response failed.
        try:
            after = self._read()
        except RowReconciliationBlocked:
            raise RowOutcomeUnknown("row write outcome unknown; replay disabled") from None
        if not self._postcondition(kind, _digest(record_id), target_digest,
                                   before.revision, after) or not self._preserves_inventory(
                                       kind, _digest(record_id), preimage, after):
            raise RowOutcomeUnknown("row write outcome unknown; replay disabled")
        self.journal.observed(action_id, after.revision)
        return after

    def reconcile(self, desired: tuple[DesiredRow, ...], *,
                  delete_ids: frozenset[str] = frozenset()) -> RowReceipt:
        desired = self._canonical_rows(desired, delete_ids)
        ids = tuple(row.record_id for row in desired)
        inventory = self.reconcile_pending_read_only()
        by_id = {row.record_id: row for row in inventory.rows}
        unknown = set(by_id) - set(ids) - set(delete_ids)
        if unknown or any(not row.managed for row in inventory.rows):
            raise RowReconciliationBlocked("unbound or unmanaged row cannot be mutated")
        adds = deletes = reorders = 0
        for rid in sorted(delete_ids):
            existing = by_id.get(rid)
            if existing:
                inventory = self._act("delete", rid, "", inventory,
                                      lambda site_id=existing.site_row_id:
                                      self.driver.delete_row(site_id))
                deletes += 1
                by_id = {row.record_id: row for row in inventory.rows}
        for row in desired:
            existing = by_id.get(row.record_id)
            if existing:
                if existing.values_digest != row.values_digest:
                    raise RowReconciliationBlocked("site row differs from canonical record")
                continue
            inventory = self._act("add", row.record_id, row.values_digest,
                                  inventory, lambda row=row: self.driver.add_row(row))
            adds += 1
            by_id = {item.record_id: item for item in inventory.rows}
            if set(by_id) - set(ids):
                raise RowReconciliationBlocked("new unbound row appeared after add")
        if tuple(row.record_id for row in inventory.rows) != ids:
            if set(by_id) != set(ids):
                raise RowReconciliationBlocked("row set incomplete before reorder")
            target = _digest(ids)
            inventory = self._act("reorder", "", target, inventory,
                                  lambda: self.driver.reorder_rows(ids))
            reorders += 1
            by_id = {row.record_id: row for row in inventory.rows}
        if (tuple(row.record_id for row in inventory.rows) != ids
                or any(not row.managed for row in inventory.rows)
                or any(by_id[row.record_id].values_digest != row.values_digest
                       for row in desired)):
            raise RowReconciliationBlocked("final row inventory differs from canonical records")
        return RowReceipt(inventory.revision, len(inventory.rows), adds, deletes,
                          reorders, _digest(ids))
