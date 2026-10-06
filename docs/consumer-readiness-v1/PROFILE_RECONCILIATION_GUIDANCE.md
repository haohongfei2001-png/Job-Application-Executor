# Profile import and explicit reconciliation guidance

This A-07 increment reports an existing admission fence; it does not change the
fence or perform reconciliation automatically. A successful JSON import may
select a complete replacement profile while an earlier unconfirmed selection
still requires the user's explicit reconciliation action.

The authenticated profile settings surface now reports that pending state.
After import, a fresh settings readback determines the guidance. When pending,
the user is directed to **打开资料并核对 → 重新读取并核对**. Opening the existing
editor only reads state. Its existing explicit reconciliation button remains the
only action that can reconcile. A false pending flag is not proof of profile
completeness, runtime health or permission to act on an application site.

If adding a task or selecting a discovered candidate hits that specific fence,
the UI explains that no task was added and offers the same existing editor entry.
The company, role and URL remain in the form. Reconciliation does not replay the
request; the user decides whether to add the task again. Unrelated conflicts
retain their existing handling, and server exception text is never displayed.

Read failures or malformed status do not claim that a save was confirmed.
Closing, Escape, or session expiry invalidates late responses and clears local
file selections. Existing profile versions, task bindings and admission checks
are unchanged. Build sequence 12 delivers the guidance; Apple identity,
notarization and consumer certification remain separate open gates.
