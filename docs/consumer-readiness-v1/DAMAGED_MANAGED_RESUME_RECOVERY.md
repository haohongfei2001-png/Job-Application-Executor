# Explicit replacement of a changed managed resume

This A-07 increment extends the existing local editor recovery path. An intact
managed canonical profile may display a read-only repair view when its nonempty
managed resume is safely readable but its bytes differ from the saved digest.
Ordinary task admission still rejects that resume. This is not permission to
accept unsafe paths, links, ownership, modes, malformed metadata, empty files,
oversized files or a changing read.

Open **资料设置 → 编辑基本资料与简历**. If a previous failed task admission
requires reconciliation, explicitly choose **重新读取并核对** first. The repair
view says the resume no longer matches its saved record and keeps the preserved
facts read-only. Choose a new PDF, DOCX or DOC and explicitly save it for future
tasks. Choosing, cancelling, closing or an expired UI session never saves.

The existing private immutable publisher creates fresh resume and profile files;
it does not overwrite, repair or delete the changed file. The previous asset
metadata remains in profile history. Existing tasks keep their original profile
reference and are not rebound, resumed or repaired by this operation.

The explicit replacement requires the exact profile/settings versions and a
bounded digest of the observed resume's actual file identity and bytes. A stale
ordinary editor cannot replace a now-damaged managed resume. Held descriptor
fences recheck the old object before and around publication. Conflicts require a
new observation and selection. A failed post-publication readback reports
uncertainty and offers reconciliation without replay or a false rollback claim.

Build sequence 11 adds this behavior only to delivered builds containing it.
Tests use synthetic private state and opaque bytes, including the real installed
service/UI in the existing unsigned DMG journey. No document parsing, external
upload, credential change or applicant action is added. Apple identity,
notarization, owner-device acceptance and consumer certification remain open.
