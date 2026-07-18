# 05. SQLite Persistence and Data Model

Status: Proposed

Previous: [`04-mail-workflows-and-consistency.md`](04-mail-workflows-and-consistency.md)
Next: [`06-mcp-interface-and-client-compatibility.md`](06-mcp-interface-and-client-compatibility.md)

## Purpose

SQLite is the local Email App's managed store. It provides durable non-secret
configuration, fast metadata search, cross-process reuse, synchronization
cursors, and operation evidence without introducing a server database.

SQLite is not authoritative for remote mailbox contents and is not a secret
vault. The schema distinguishes durable local configuration and operation
evidence from rebuildable mail indexes and caches.

## Storage Classes

| Class                         | Examples                                                  | Rebuildable                    | Default persistence            |
| ----------------------------- | --------------------------------------------------------- | ------------------------------ | ------------------------------ |
| Managed configuration         | Accounts, endpoints, policies, secret bindings            | No                             | Yes                            |
| Operational identity          | Stable IDs and legacy/environment source mappings         | No, when evidence refers to it | Yes in every base mode         |
| Operation evidence            | SMTP acceptance, uncertain outcome, reconciliation state  | No                             | Yes, bounded retention         |
| Mail index                    | Mailboxes, message metadata, placements, flags, addresses | Yes, from IMAP                 | Yes                            |
| Search projection             | FTS subject, addresses, preview, permitted cached text    | Yes                            | Yes when available             |
| Body cache                    | Normalized plain-text prefixes                            | Yes                            | Bounded and policy-controlled  |
| Attachment metadata           | Filename, MIME type, part locator, size                   | Yes                            | Yes                            |
| Raw MIME and attachment bytes | Provider payloads                                         | Yes                            | No by default                  |
| Temporary artifacts           | Explicit local materialization                            | Yes                            | Private filesystem with expiry |

## Sources of Truth

- Managed account configuration is authoritative in SQLite only after an
  `ACTIVE` managed catalog has been explicitly initialized.
- `ACCOUNT_IDENTITY` and `ACCOUNT_SOURCE` are authoritative only for local
  operational continuity; they never override legacy or environment base
  configuration.
- The selected `SecretStore` is authoritative for secret values. SQLite stores
  `SecretRef` metadata only.
- IMAP is authoritative for remote mailboxes, placements, flags, bodies, and
  attachments.
- SMTP response evidence is authoritative for known delivery acceptance.
- SQLite operation records are authoritative for what the local application
  observed and which reconciliation steps remain.

## Logical Schema

```mermaid
erDiagram
    ACCOUNT_IDENTITY ||--o{ ACCOUNT_SOURCE : resolves
    ACCOUNT_IDENTITY ||--o| MANAGED_ACCOUNT : configures
    MANAGED_CATALOG ||--|| GLOBAL_POLICY : governs
    MANAGED_CATALOG ||--o{ MANAGED_ACCOUNT : owns
    MANAGED_CATALOG ||--o{ POLICY_RULE_SET : defines
    MANAGED_ACCOUNT ||--|| ACCOUNT_POLICY : overrides
    MANAGED_ACCOUNT o|--o{ POLICY_RULE_SET : scopes
    POLICY_RULE_SET ||--o{ POLICY_RULE : contains
    MANAGED_ACCOUNT ||--o{ MAIL_ENDPOINT : configures
    MANAGED_ACCOUNT ||--o{ CREDENTIAL_BINDING : references
    MANAGED_ACCOUNT ||--o{ SECRET_CHANGE : changes
    ACCOUNT_IDENTITY ||--o{ MAILBOX : owns
    ACCOUNT_IDENTITY ||--o{ MESSAGE : indexes
    ACCOUNT_IDENTITY ||--o{ OPERATION : records
    MAILBOX ||--o{ MESSAGE_PLACEMENT : contains
    MESSAGE ||--o{ MESSAGE_PLACEMENT : placed_as
    MESSAGE ||--o{ MESSAGE_ADDRESS : addresses
    MESSAGE ||--o{ MESSAGE_BODY : caches
    MESSAGE ||--o{ ATTACHMENT : describes
    MAILBOX ||--o| SYNC_CURSOR : tracks
    OPERATION ||--o{ OPERATION_ATTEMPT : attempts

    ACCOUNT_IDENTITY {
        text id PK
        text display_name
        text state
        datetime first_seen_at
        datetime last_seen_at
        datetime retired_at
    }
    ACCOUNT_SOURCE {
        text id PK
        text account_id FK
        text source_kind
        text source_namespace
        text source_key
        integer fingerprint_version
        text source_fingerprint
        text state
        datetime first_seen_at
        datetime last_seen_at
    }
    MANAGED_CATALOG {
        text id PK
        text lifecycle_state
        integer generation
        integer revision
        integer policy_version
        datetime updated_at
    }
    GLOBAL_POLICY {
        text catalog_id PK, FK
        integer revision
        text default_secret_backend
        text metadata_index_scope
        integer report_blocked_mutations
        integer attachment_download_enabled
        integer metadata_quota_bytes
        integer body_cache_quota_bytes
        integer artifact_quota_bytes
    }
    MANAGED_ACCOUNT {
        text account_id PK, FK
        text catalog_id FK
        text account_name UK
        text full_name
        text email_address
        text status
        integer revision
        datetime deleted_at
        datetime created_at
        datetime updated_at
    }
    ACCOUNT_POLICY {
        text account_id PK, FK
        integer revision
        text freshness_policy
        text mutation_policy
        integer body_cache_enabled
        datetime updated_at
    }
    POLICY_RULE_SET {
        text id PK
        text catalog_id FK
        text account_id FK
        text kind
        text mode
        integer revision
    }
    POLICY_RULE {
        text id PK
        text rule_set_id FK
        text normalized_value
        text effect
        integer ordinal
        integer enabled
    }
    MAIL_ENDPOINT {
        text id PK
        text account_id FK
        text role
        text host
        integer port
        text tls_mode
        integer verify_tls
        text username
    }
    CREDENTIAL_BINDING {
        text id PK
        text account_id FK
        text purpose
        text backend
        text locator
        text version
        text state
        datetime created_at
        datetime updated_at
    }
    SECRET_CHANGE {
        text id PK
        text account_id FK
        text purpose
        text kind
        integer expected_account_revision
        text old_binding_id FK
        text candidate_binding_id FK
        text state
        text error_code
        datetime created_at
        datetime updated_at
    }
    MAILBOX {
        text id PK
        text account_id FK
        text remote_name
        text delimiter
        text attributes_json
        integer uidvalidity
        integer uidnext
        datetime indexed_at
    }
    MESSAGE {
        text id PK
        text account_id FK
        text rfc_message_id
        text subject
        datetime sent_at
        datetime internal_date
        integer size_bytes
        text preview
        datetime last_observed_at
    }
    MESSAGE_PLACEMENT {
        text mailbox_id FK
        text message_id FK
        integer uidvalidity
        integer uid
        text flags_json
        integer modseq
        datetime observed_at
    }
    MESSAGE_ADDRESS {
        text message_id FK
        text role
        integer ordinal
        text display_name
        text address
    }
    MESSAGE_BODY {
        text message_id FK
        text body_kind
        text content_prefix
        integer source_chars
        integer prefix_chars
        text completeness
        datetime cached_at
        datetime expires_at
    }
    ATTACHMENT {
        text id PK
        text message_id FK
        text part_locator
        integer ordinal
        text filename
        text media_type
        text content_id
        integer size_bytes
        text checksum
    }
    SYNC_CURSOR {
        text mailbox_id PK
        integer uidvalidity
        integer highest_uid
        integer highest_modseq
        text coverage_state
        datetime coverage_start
        integer revision
        datetime updated_at
    }
    OPERATION {
        text id PK
        text account_id FK
        text kind
        text idempotency_key
        text payload_hash
        text state
        text result_json
        datetime created_at
        datetime updated_at
    }
    OPERATION_ATTEMPT {
        text id PK
        text operation_id FK
        integer attempt_number
        text claim_token
        text phase
        datetime claim_deadline
        datetime remote_started_at
        text outcome
        text error_code
        datetime started_at
        datetime finished_at
    }
```

This is a logical schema. Physical migrations may combine narrowly related
tables or add generated columns when measured query behavior justifies it. They
must preserve identity, secret, and transaction invariants.

## Operational Account Identity

`ACCOUNT_IDENTITY.id` is the stable local `account_id` used by mailboxes,
messages, cursors, and operations in every base mode. It contains no endpoint,
policy, or secret configuration and therefore does not make the database a
second account-config source in legacy mode. Its `display_name` is diagnostic
metadata and is never used instead of effective-source resolution. Operational
identity rows and mail index rows may exist when no managed catalog is active.
Identity state is `ACTIVE` while any selected source or managed account can use
it and `RETIRED` when only retained index or evidence refers to it; retirement
does not itself delete evidence.

`ACCOUNT_SOURCE` maps a non-managed source to that identity. A source mapping is
unique on `(source_kind, source_namespace, source_key)`:

- for legacy TOML, the namespace is a stable local digest of the logical
  configuration source and the key is the stored account entry key. The current
  and legacy default paths are recognized aliases; an explicit custom-path move
  transfers or rebinds the namespace rather than silently creating identities;
- for the environment account, the namespace identifies the documented
  environment slot and the key is its compatibility account name;
- `source_fingerprint` is a versioned digest of the exact non-secret identity
  tuple defined below; it detects accidental source-key reuse without storing a
  copy of source configuration.

For a legacy source, `source_namespace` is encoded as
`legacy-path-v1:<sha256>`. The recognized current and legacy default paths map to
the same logical input before hashing. A custom path expands the user directory,
becomes absolute, resolves existing symlinks, normalizes separators and the
platform's filesystem case semantics, and then uses a length-prefixed UTF-8 path
encoding. If the target does not yet exist, the existing parent is resolved and
the final component retained. `source_key` is the validated stored account key
after trim and Unicode NFC normalization with case preserved; the environment
slot uses the same account-name rule. Moving a custom source or retargeting a
symlink therefore requires the explicit namespace transfer/rebind already
defined above.

Fingerprint version 1 for an email account is the SHA-256 digest of a
domain-separated, length-prefixed UTF-8 encoding of:

```text
("email-v1", normalized_email_address, incoming_host, incoming_port,
 incoming_username, incoming_tls_mode)
```

Canonicalization is normative:

- email uses the current compatibility address parser, surrounding whitespace
  removal, and lowercase normalization;
- host is trimmed, converted to a lowercase IDNA A-label, and has one terminal
  DNS dot removed;
- port is the validated integer encoded in base-10 without leading zeroes;
- username is trimmed and Unicode NFC-normalized but remains case-sensitive;
- TLS is the normalized `implicit | starttls | none` enum.

Account/display name, full name, password, secret locator, outgoing endpoint,
and mutable policy are excluded. Changing an included field is
identity-affecting and enters the explicit rebind-or-successor conflict flow;
changing an excluded field does not. Another provider kind must define its own
versioned stable principal/endpoint tuple before it may persist operational
identity.

`fingerprint_version` is stored beside the digest. An algorithm upgrade supports
the old and new versions during migration, re-reads the authoritative source,
and compare-and-swaps to the new digest only when the old digest, source key, and
identity mapping all match uniquely. An absent source remains on the old version;
an ambiguous or unverifiable source remains conflicted. Upgrade code never
infers equivalence from display name and never persists the canonical tuple or a
secret in SQLite.

An exact source key, fingerprint version, and digest reuses the identity after
restart. If a key reappears with a materially different fingerprint, the adapter
marks a conflict
and requires an explicit CLI rebind or successor identity; it never silently
attaches old mail or operation evidence. Before creating a new identity, it also
checks for the same fingerprint under another key in that source namespace. Such
a probable out-of-band rename is a conflict and blocks provider-effect operations
until the user chooses rebind or explicitly accepts a successor identity and the
associated idempotency-evidence discontinuity.

A source that disappears updates `last_seen_at` and is retired only through
explicit index maintenance. A CLI-managed legacy rename updates the source
mapping with the file change; an out-of-band rename follows the conflict flow
above rather than silently becoming a new account.

An environment account that shadows the same display name as a managed or legacy
account keeps a distinct source mapping and identity. Name resolution selects the
attributed effective source, not whichever identity was seen first. Diagnostics
show both the selected and shadowed identities.

`MANAGED_ACCOUNT.account_id` is both its primary key and a foreign key to
`ACCOUNT_IDENTITY`. New managed accounts create both rows. A legacy-to-managed
import reuses the selected legacy identity for its staging managed row whenever
the source match is unambiguous, preserving index and operation continuity; the
legacy source mapping remains as provenance and is not a writable managed
configuration row.

## Managed Configuration

### Catalog lifecycle

`MANAGED_CATALOG.lifecycle_state` is one of `STAGING`, `ACTIVE`, `MAINTENANCE`,
or `FAILED_IMPORT`. `ACTIVE` selects managed base mode; `MAINTENANCE` selects a
management-only runtime and must not fall back to legacy mail access. Staging and
failed imports may coexist with an operational legacy index but are never visible
as the effective account catalog. At most one catalog is active or in maintenance
and at most one unfinished staging import exists.

`generation` changes when a process must rebuild its base configuration adapter;
`revision` changes for ordinary writes within one active catalog. Legacy mode
with neither an active nor maintenance catalog uses generation zero. Activating a
staging catalog or entering or leaving maintenance advances generation in the
same transaction that changes lifecycle state.

For a mutation, the transition from `CLAIMED_PRE_EFFECT` to
`REMOTE_EFFECT_POSSIBLE` or `SUBMITTING` conditionally verifies the process's
startup generation in that same SQLite transaction. If it changed, the
transition fails with `RESTART_REQUIRED` and no provider side effect occurs. A
provider call that crossed its boundary before the generation transition may
finish and reconcile from its original snapshot; every later independent or
compound side-effect substep checks generation again.

### Scalar and rule policy

`GLOBAL_POLICY` owns typed scalar settings, including blocked-mutation reporting,
attachment enablement, and cache or artifact quotas. `ACCOUNT_POLICY` owns typed
per-account overrides such as freshness, mutation, and body-cache policy. For
scalar fields, an explicitly set account value replaces the global value, and a
supported process environment override replaces that result using current
compatibility semantics. Every effective scalar carries its final source.

`POLICY_RULE_SET` makes absence, inheritance, unrestricted access, and an
explicit empty restriction distinguishable. Each global or account scope has at
most one set per `kind`; its `mode` is:

- `INHERIT`: this scope adds no allow constraint;
- `UNRESTRICTED`: explicitly no allow constraint at this scope;
- `RESTRICT`: every request must match an enabled `allow` rule in this scope; a
  restrictive set with zero allow rows denies all.

Absence has scope-specific, non-ambiguous semantics:

- every `ACTIVE` catalog must materialize exactly one global set for every
  supported security-sensitive kind; a missing global set is invalid
  configuration, never implicit unrestricted access;
- catalog activation and startup validation fail if a required global set is
  missing, duplicated, has an unknown mode, or uses global `INHERIT`;
- while invalid, mail/provider operations, indexed content exposure, and
  filesystem materialization that depend on policy are disabled; read-only
  `doctor` may identify the missing set and maintenance CLI may repair it;
- an account-scoped row may be absent, which is implicit `INHERIT`; an explicit
  account `INHERIT` row has the same authorization result but preserves a
  revisioned user choice for diagnostics.

Managed catalog initialization materializes these versioned defaults:

| Global kind            | Initial mode and rules                                               |
| ---------------------- | -------------------------------------------------------------------- |
| `recipient_address`    | `UNRESTRICTED`, preserving the current empty-recipient-list behavior |
| `sender_pattern`       | `UNRESTRICTED`, preserving the current empty-sender-list behavior    |
| `approved_input_root`  | `RESTRICT` to the canonical private import/workspace root            |
| `approved_output_root` | `RESTRICT` to the canonical private artifact workspace               |

A staging catalog may be incomplete while it is built, but the final activation
transaction validates the full required set and its canonical root values.
Migrations that introduce a kind materialize its declared default before the
catalog can remain `ACTIVE`. Database damage or an unknown future kind fails
closed rather than being interpreted as `INHERIT` or `UNRESTRICTED`.

`POLICY_RULE` owns the normalized values and `allow` or `deny` effect. Enabled
deny rules from all durable scopes are unioned and always win. Account allow
rules cannot override a global deny. When both global and account sets are
`RESTRICT`, a request must satisfy both sets, so an account can narrow but cannot
expand global authority.

The initial rule kinds have these complete semantics:

| Rule kind              | Normalized match                                           | Global/account behavior                                              | Environment behavior                                                                                                                                |
| ---------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipient_address`    | Exact normalized envelope address                          | Every restrictive scope must match; any matching deny wins           | Present `MCP_EMAIL_SERVER_ALLOWED_RECIPIENTS` replaces durable allow constraints; an empty value means unrestricted, while durable denies still win |
| `sender_pattern`       | One normalized sender against an anchored case-folded glob | Every restrictive scope must match; malformed/multiple sender fails  | Present `MCP_EMAIL_SERVER_ALLOWED_SENDERS` replaces durable allow constraints; an empty value means unrestricted, while durable denies still win    |
| `approved_input_root`  | Canonical target is contained by the canonical root        | Every restrictive scope must contain the target; denied subtree wins | No compatibility override; an environment value cannot implicitly add a root                                                                        |
| `approved_output_root` | Canonical target is contained by the canonical root        | Every restrictive scope must contain the target; denied subtree wins | No compatibility override; an environment value cannot implicitly add a root                                                                        |

The two documented allowlist variables intentionally remain process-global,
full process-local replacements for every effective account, including their
existing present-but-empty behavior. This explicit local environment authority
can expand a managed allow constraint, so account summaries and `doctor` report
the replacement and its source. It still cannot override a durable deny rule.
Legacy TOML empty allowlists map to
`UNRESTRICTED`; managed callers use `INHERIT`, `UNRESTRICTED`, or an empty
`RESTRICT` set explicitly rather than overloading one empty list.

Rule evaluation never uses first-match wins. `ordinal` preserves deterministic
CLI display and diagnostics only. An authorization result attributes every
restrictive set, matching deny, and environment replacement that participated;
it does not report only one nominal winner. Unsupported kinds, effects, or
wildcard grammars fail validation, and a new security-sensitive rule kind cannot
ship until this table defines its merge and environment behavior.

Rule-set and scalar policy writes use expected revisions just like account
writes. Unique and check constraints prevent duplicate normalized values and
unsupported effects. A catch-all JSON settings blob must not bypass validation,
revisions, source attribution, or migrations.

### Accounts and removal

`MANAGED_ACCOUNT.account_id` is an application-generated stable identifier shared
with its operational identity. `account_name` is a unique user-facing name, not
the primary identity. Rename increments `revision` and does not re-key messages,
operations, source mappings, or secret bindings.

`account remove` is a soft-delete workflow:

1. change status to `REMOVED` and set `deleted_at` using the expected revision;
2. reject new mail operations while retaining the stable ID and minimum
   non-secret identity;
3. purge rebuildable mailbox, message, body, attachment, and FTS rows separately;
4. complete credential cleanup through the persisted secret-change saga;
5. retain the managed tombstone and operational identity with unresolved or
   retained operation evidence.

`account_name` is not reusable while the tombstone remains. A separate
destructive hard purge runs only after explicit confirmation of the backup and
evidence-loss warning. In one guarded transaction it:

1. rechecks the expected tombstone revision and exclusive maintenance lock;
2. proves that no active or incomplete secret change, live credential binding,
   unresolved/unknown operation, or non-retention-eligible evidence remains;
3. explicitly deletes retention-eligible completed operation attempts and
   operations;
4. deletes completed secret-change and deleted-binding metadata after external
   secret cleanup is proven;
5. deletes rebuildable index rows, account rule sets and rules, policy,
   endpoints, and the managed account row in dependency order;
6. retires or deletes source mappings as requested and deletes the operational
   identity only when no reference remains;
7. releases the unique name only with the tombstone deletion.

Every reference is `RESTRICT` by default. The command uses enumerated deletes and
must not enable a blind cascade over the account graph.

### Endpoints

`MAIL_ENDPOINT.role` is constrained to supported roles such as `incoming` and
`outgoing`. `(account_id, role)` is unique. TLS behavior is represented as a
validated enum such as `implicit`, `starttls`, or `none`; contradictory booleans
are normalized at the adapter boundary.

### Credential bindings and change recovery

`CREDENTIAL_BINDING` contains no resolved value. Its lifecycle is
`CANDIDATE -> ACTIVE -> OLD_DELETE_PENDING -> DELETED`. A partial unique index
permits exactly one `ACTIVE` row for `(account_id, purpose)` while retaining the
independent candidate and cleanup rows. Before the external value write, managed
mutable backends allocate a unique locator/version and the application persists
the `CANDIDATE` binding; the active locator is never modified in place.

`SECRET_CHANGE` persists kind, expected account revision, old and candidate
binding IDs, state, and bounded error code. `SET`, `ROTATE`, and `IMPORT` use
`PREPARED -> CANDIDATE_WRITTEN -> BINDING_COMMITTED -> CLEANUP_PENDING -> COMPLETED`.
`DELETE` has a null candidate and, only after the account is durably disabled,
uses `PREPARED -> BINDING_COMMITTED -> CLEANUP_PENDING -> COMPLETED` while the old
binding advances to `OLD_DELETE_PENDING`. A partial unique index permits only one
incomplete change per account and purpose. Candidate creation, binding
activation, old-secret deletion, account removal, and legacy import all use this
record for crash recovery. Binding and change states are separate vocabularies;
`OLD_DELETE_PENDING` is never a `SECRET_CHANGE` state.

The `locator` is sensitive operational metadata even though it is not the secret
value; it is never exposed through MCP or normal CLI listing. `credential
repair` operates from binding/change rows and does not require keyring
enumeration. Secret deletion by immutable locator is idempotent; not-found is
accepted only when the committed binding state proves the locator is no longer
active. A database constraint cannot prove that an external value exists, so
`doctor` and account tests resolve bindings safely without logging values.

### Core constraints and delete behavior

- `ACCOUNT_SOURCE` is unique on `(source_kind, source_namespace, source_key)`;
  fingerprint version or digest mismatch follows the verified migration or
  conflict path, never an upsert onto old evidence.
- `MANAGED_ACCOUNT.account_name` remains unique across active and retained
  tombstone rows.
- Operational `MAILBOX`, `MESSAGE`, and `OPERATION` rows reference
  `ACCOUNT_IDENTITY`, never `MANAGED_CATALOG`.
- `MAIL_ENDPOINT` is unique on `(account_id, role)`.
- `ACCOUNT_POLICY` is one-to-one with managed account; global and account policy
  revisions advance independently.
- `POLICY_RULE_SET` is unique on `(catalog_id, account_id, kind)`, with null-safe
  uniqueness for global scope. A composite constraint ensures an account-scoped
  set references a managed account in the same catalog. `POLICY_RULE` is unique
  on `(rule_set_id, normalized_value, effect)` and has a deterministic ordinal.
- Exactly one active credential binding and one incomplete secret change may
  exist per managed account and purpose.
- Account removal does not cascade to operation evidence, credential cleanup,
  operational identity, or the managed tombstone.
- Rebuildable mailbox, message, body, attachment, and FTS rows may be purged by
  an explicit index cleanup after soft removal.
- Every account-graph foreign key uses `RESTRICT` unless a narrow rebuildable
  child relation documents otherwise. Hard purge follows the enumerated guarded
  deletion order and never relies on a blind cascade.

## Mail Index

### Mailboxes and placements

A mailbox is unique on `(account_id, remote_name)`. A placement is unique on:

```text
(mailbox_id, uidvalidity, uid)
```

`MESSAGE_PLACEMENT` also has an index on `(message_id, mailbox_id)`. When
UIDVALIDITY changes, old placements for that mailbox become invalid and are
replaced through controlled rebuild. They are never matched to new UIDs by UID
alone.

### Messages

`MESSAGE.id` is local and opaque. `rfc_message_id` is nullable and indexed but
not unique. Message coalescing across mailboxes must use provider evidence or a
conservative reconciliation rule; matching only the RFC header is insufficient.

Useful indexes include:

- `(account_id, internal_date, id)` for stable recent-mail ordering;
- `(account_id, rfc_message_id)` for thread and sent-copy reconciliation;
- `(account_id, subject)` only if query plans justify it;
- placement indexes for mailbox and UID lookup.

### Addresses

Addresses are normalized into `MESSAGE_ADDRESS` so sender and recipient filters,
allowlists, and search do not depend on parsing JSON at query time.

- `role` is constrained to `from`, `sender`, `reply_to`, `to`, `cc`, or `bcc` as
  appropriate to indexed data.
- `(message_id, role, ordinal)` is unique.
- normalized `address` is indexed with `role` for common filters.
- BCC for received remote messages is stored only when it is legitimately
  present; it is not inferred.

### Body cache

`MESSAGE_BODY` stores decoded, normalized text only under explicit cache policy.
Raw MIME and binary payloads are excluded. `(message_id, body_kind)` is unique.

The cached text is always a contiguous prefix beginning at normalized character
offset zero. `prefix_chars` is the exclusive prefix end. `completeness`
distinguishes `prefix`, `complete`, and `truncated_by_policy`, and
`source_chars` records full normalized length when known. A non-prefix body
request may be served from a complete cached prefix; otherwise it is fetched
without being persisted as a misleading standalone window.

MCP character offsets are applied after bounded MIME decoding. Mail adapters use
provider byte ranges only when they can map them safely through transfer and
character encodings; otherwise they enforce an input-byte budget while decoding
and return `partial` rather than reading an unbounded MIME part.

Body cache may be disabled, limited to recent messages, or capped by bytes and
age. Metadata indexing remains useful when body cache is disabled.

### Attachments

`ATTACHMENT` stores only metadata and a provider-relative MIME part locator.
`(message_id, part_locator)` and `(message_id, ordinal)` are unique where the
provider data supports those constraints. Filenames are display metadata, not
storage keys.

## Full-text Search

When SQLite FTS5 is available, a derived `message_fts` projection indexes only
policy-approved fields:

- subject;
- normalized sender and recipient addresses;
- preview;
- cached normalized body text when body indexing is enabled.

The FTS row references `MESSAGE.id`. Repository transactions update the content
row and search projection together or mark the projection stale for repair.
FTS content is rebuildable from base index rows.

FTS5 availability is checked at startup. If unavailable, the app remains
functional with indexed filters and provider search; it reports reduced search
capability rather than failing the entire runtime.

A search response states whether body text was in the indexed field set. It does
not imply full-message search when only metadata coverage exists.

## Synchronization Cursors

`SYNC_CURSOR` is mailbox-scoped. It stores UIDVALIDITY, highest observed UID,
optional highest MODSEQ, coverage state, coverage boundary, and a monotonic
revision.

A synchronization batch follows this order:

1. read cursor and revision;
2. perform bounded IMAP discovery outside a transaction;
3. begin a short write transaction;
4. verify the cursor revision still matches;
5. upsert mailboxes, messages, addresses, placements, and attachments;
6. update search projections;
7. advance cursor and coverage in the same commit.

If the revision changed, the process re-evaluates or safely repeats idempotent
upserts. It never commits a cursor that skips metadata it did not persist.

## Operation Journal

The operation journal stores durable local evidence only for workflows where it
prevents unsafe replay or supports reconciliation.

Required constraints:

- unique `(account_id, kind, idempotency_key)` when a key is present;
- the same key may be reused only with the same payload hash;
- unique `(operation_id, attempt_number)`;
- one active attempt claim per operation, protected by a random claim token,
  conditional transitions, and a bounded stale deadline;
- an attempt phase that distinguishes `CLAIMED_PRE_EFFECT` from
  `REMOTE_EFFECT_POSSIBLE` before any provider side-effect call;
- the effect-boundary transition conditionally verifies both claim token and
  startup catalog generation in the same transaction; a mismatch produces
  `RESTART_REQUIRED` without advancing the attempt;
- per-recipient SMTP acceptance, rejection, and unknown evidence in bounded
  typed result data;
- explicit sent-copy and compound-IMAP substep outcomes;
- result JSON never contains full bodies, MIME, attachment bytes, or secrets.

A stale `CLAIMED_PRE_EFFECT` attempt may be fenced and reclaimed because it did
not cross a remote-effect boundary. A stale `REMOTE_EFFECT_POSSIBLE` attempt is
converted to `OUTCOME_UNKNOWN` and is never automatically replayed. Claim expiry
is a recovery signal, not proof that the provider did nothing.

`OUTCOME_UNKNOWN`, partial acceptance, pending compound-operation substeps, and
confirmed remote success records use longer retention than rebuildable cache
rows. They retain their `ACCOUNT_IDENTITY` foreign key; a removed managed account
also retains its tombstone until guarded hard purge. Purging evidence requires an
explicit policy and must not make the application claim stronger idempotency
guarantees.

## SQLite Runtime Rules

- Enable foreign keys for every connection.
- Use WAL mode, a bounded busy timeout, and documented synchronous settings.
- Keep one clear connection ownership strategy per process; do not share a raw
  connection across unrelated async tasks without serialization.
- Hide synchronous driver work behind an adapter that does not block the event
  loop for unbounded periods.
- Keep write transactions short and deterministic.
- Never perform IMAP, SMTP, keyring, DNS, or large filesystem work in a
  transaction.
- Parameterize every value; mailbox names, search terms, and message data never
  become SQL fragments.
- Use explicit projections rather than returning `SELECT *` rows across the
  repository boundary.
- Use `PRAGMA integrity_check` or an appropriate bounded check in explicit
  diagnostics, not on every startup.

## Schema Migrations

A `schema_migrations` table records version, name, checksum, and applied time.
Migrations are forward-only in normal operation.

- A local lock prevents two processes from migrating the same database.
- Startup waits only for a bounded period and reports a clear maintenance error.
- Every migration runs in a transaction when SQLite permits it.
- Destructive transformations copy and validate data before dropping old
  structures.
- A failed migration leaves the last committed schema readable or marks the
  database as requiring explicit repair.
- The application refuses to write a newer unsupported schema.
- Migration tests start from every supported prior schema fixture.

## Files and Permissions

The database and private artifact directory are created with owner-only
permissions where the platform supports them. Parent directories are private.

SQLite sidecars such as `-wal` and `-shm` receive equivalent directory
protection. The application does not claim database encryption at rest; users
who require it rely on operating-system disk encryption unless a separately
accepted design introduces application-level encryption.

## Retention and Quotas

Independent limits apply to:

- indexed message age or count per account;
- body cache bytes and age;
- operation evidence age by state;
- temporary artifact bytes and expiry;
- synchronization batch items, bytes, and time.

Default policy principles:

- index metadata by default within a configured local budget;
- cache bodies only on demand and under an explicit bound;
- do not cache attachment bytes by default;
- retain unresolved or uncertain operation evidence until resolved or explicitly
  acknowledged;
- purge derived FTS rows with their source cache rows;
- report partial index coverage after quota eviction.

Concrete numeric defaults remain a product configuration decision and must be
specified with implementation benchmarks rather than guessed in this proposal.

## Backup, Restore, and Rebuild

A SQLite backup includes managed non-secret configuration, operational account
identities and source mappings, index state, and operation evidence. It does not
include keyring secret values or temporary artifacts.

- Use SQLite's online backup API or an equivalent consistent method; copying only
  the main file while WAL is active is not a supported backup procedure.
- Restore validates schema version and secret bindings before enabling mail
  operations.
- Missing rebuildable index rows trigger refresh.
- Missing secret bindings disable affected accounts and produce CLI remediation;
  they do not trigger plaintext fallback.
- Missing temporary artifacts are reported as expired and can be fetched again.
- `index rebuild` preserves operational identities, source mappings, managed
  accounts, credential bindings, and operation evidence while replacing only
  rebuildable mail data.

## Validation

Persistence tests cover:

- all constraints, indexes, foreign keys, and delete behavior;
- migration from every supported fixture and migration checksum mismatch;
- concurrent readers, writer contention, and bounded busy behavior;
- sync revision conflicts and atomic cursor advancement;
- UIDVALIDITY invalidation;
- legacy and environment identity reuse across restart; exact v1 canonical
  tuples; address, IDNA host, username, TLS, path, case, symlink, and default-path
  normalization; fingerprint-version migration; conflict behavior; source
  disappearance/reappearance; rename/rebind; shadowing; and legacy-to-managed
  import without evidence re-keying;
- `STAGING`/`FAILED_IMPORT` catalogs never selecting managed mode, atomic `ACTIVE`
  generation transition, and repair of pre-recorded import candidates;
- account rename, soft removal, retained operation evidence, and guarded hard
  purge with explicit completed-child deletion and no blind cascade;
- required global rule-set completeness at activation and startup; invalid or
  missing global sets failing closed; account absence as inherit; every policy
  mode; global/account allow intersections; deny-wins; explicit empty
  restrictions; current environment full-replacement and empty semantics; root
  containment; deterministic diagnostics; and source attribution;
- secret-change crashes at every state, concurrent rotation fencing, pending
  cleanup repair, and proof that the active locator is never deleted;
- FTS enabled and unavailable paths;
- body and artifact quota eviction;
- operation idempotency, atomic generation/effect-boundary ordering for each
  compound substep, stale pre-effect claim recovery, stale post-boundary
  conversion to unknown, partial SMTP acceptance, and uncertain-outcome retention;
- database, WAL, and artifact permissions;
- online backup, restore, index rebuild, and missing-secret recovery;
- proof that resolved secret values never reach database pages through normal
  application writes.
