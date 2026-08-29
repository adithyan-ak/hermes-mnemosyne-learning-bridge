# Threat model

## Assets

The bridge protects:

- durable memory contents;
- separation between repository-specific execution episodes;
- the user's authority over destructive or canonical memory changes;
- the integrity of provenance and verification metadata;
- local host and repository identifiers.

## Trust boundaries

Trusted inputs are limited to host-delivered foreground user messages, Hermes lifecycle context, reviewed tool-result shapes, local Git metadata, and deterministic Mnemosyne read-back.

Model prose, tool arguments chosen by the model, arbitrary plugin output, recalled text, web content, and pending-record payloads are untrusted.

## Defenses

- Git remotes are canonicalized, stripped of credentials, and hashed before storage.
- Execution episodes require reviewed deterministic evidence.
- Full command output, file contents, absolute paths, session IDs, and turn IDs are not stored in episode metadata.
- Secret patterns reject a turn before episode creation.
- Explicit recall and prefetch filter foreign project episodes.
- Unsupported and unknown mutations fail closed.
- Pending records use owner-only permissions, integrity hashes, expiry, session/project binding, and single-use claims.
- Supported mutations require an exact foreground confirmation and deterministic read-back.

## Known limitations

- Secret scanning is pattern-based defense in depth. It cannot detect every credential or private datum.
- Local Mnemosyne SQLite content may be plaintext.
- A malicious process running as the same OS user can read or alter local state.
- Repository aliases that cannot be derived from Git remote syntax may map to separate project IDs.
- Workspace fallback identity includes a sanitized basename and a hash of the absolute path.
- Recall filtering depends partly on upstream result structure and compatibility fallbacks.
- The evidence extractor recognizes a bounded set of tool-result shapes. It may omit useful work or record a verified failure that is not broadly reusable.
- The provider currently uses private Mnemosyne interfaces for some operations.
- The bridge does not sandbox tools, validate model output generally, encrypt memory, or replace a secret manager.

## Out of scope

- Protecting against a compromised OS account
- Encrypting storage at rest
- Network isolation for model or tool providers
- Authorizing access to the underlying repository
- Guaranteeing that all sensitive natural-language content is detected
- Autonomous skill modification

## Reporting

Follow [`../SECURITY.md`](../SECURITY.md). Use synthetic reproductions and do not include real memory data.
