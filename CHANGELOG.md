# Changelog

All notable changes to this project are documented here.

## 0.2.2 - 2026-09-01

- Ground automatic ordinary-memory writes in a verbatim span of the current user turn.
- Reject secret-bearing direct writes before Mnemosyne storage.
- Bind project identity to Hermes's session-scoped runtime workspace when available.
- Refill explicit recall after foreign-project results are removed.
- Allow approved corrections to global ordinary memories from later sessions.
- Keep foreground turn start independent of the background episode-writer lock.
- Align prompt guidance with the bridge's restricted mutation surface.
- Exclude version/help/collection probes from verified test evidence.

## 0.2.1 - 2026-08-31

- Write explicit user-stated ordinary memories directly with exact deterministic read-back.
- Return `clarification_required` for ambiguous ordinary-memory payloads instead of silently omitting or staging them.
- Keep updates and deletions behind foreground-bound staged approval.

## 0.2.0 - 2026-08-29

Initial public alpha.

- Stores compact execution episodes from deterministic tool evidence.
- Filters project-scoped episodes during recall and prefetch.
- Stages supported mutations with exact review payloads, foreground confirmation, and read-back.
- Normalizes equivalent SSH and HTTPS Git remotes before hashing project identity.
- Removes local paths, session IDs, and turn IDs from durable episode metadata.
- Writes episodes through a bounded background queue so `sync_turn()` remains non-blocking.
- Disables upstream automatic role sync, built-in-memory mirroring, turn consolidation, and session-end consolidation at the provider boundary.
- Prevents one apply attempt from deleting another call's claimed mutation.
- Expands credential detection while keeping secret scanning explicitly defense in depth.
