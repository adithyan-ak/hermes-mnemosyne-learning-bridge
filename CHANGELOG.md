# Changelog

All notable changes to this project are documented here.

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
