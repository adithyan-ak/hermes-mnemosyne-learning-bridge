# Security policy

## Supported version

The latest tagged alpha receives security fixes. The project is pre-1.0 and may change interfaces between releases.

## Report a vulnerability

Use GitHub's private vulnerability reporting for this repository. Include:

- affected version and commit;
- the trust boundary that failed;
- a minimal synthetic reproduction;
- whether memory contents, project isolation, or mutation approval are affected;
- a proposed mitigation if you have one.

Do not include real memories, credentials, local paths, private repository names, or production configuration.

## Security expectations

This bridge reduces accidental retention and cross-project recall. It is not a secret manager, sandbox, encryption layer, or authorization service. Secret patterns are defense in depth and will have false negatives. Mnemosyne's local SQLite data should be treated as plaintext unless the operator adds storage encryption.

The bridge currently depends on specific upstream behavior and some non-public Mnemosyne implementation details. Review the compatibility notes before upgrading dependencies.
