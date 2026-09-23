# ADR 006: Gmail Move Semantics and an Explicit Trash Action

**Status:** Accepted

## Context

The filter moves a message on a generic IMAP server by copying it to the destination mailbox, marking the source `\Deleted`, and running a single `EXPUNGE` after the account's processing loop. This is the verified baseline for non-Gmail accounts.

Gmail represents folders as labels. `COPY` to a label adds that label to the message; the message remains one object in All Mail. Removing a message from `INBOX` by `\Deleted` and `EXPUNGE` is interpreted through the account's IMAP settings: Auto-Expunge decides whether `\Deleted` expunges immediately, and a separate setting decides whether a message expunged from its last visible folder is archived, moved to Trash, or deleted forever. The generic move therefore has setting-dependent results on Gmail and places `\Deleted` on a message that stays visible under its destination label.

The same settings make the existing `delete` action ambiguous on Gmail: by default, a message deleted from `INBOX` is archived, not trashed. The rule language had no action meaning "put this message in the provider's Trash."

Trash also differs from the folders that rules route mail into. Those folders are organized by the account owner, so only configuration can say where a logical key such as `services_payments` lives ([ADR 003](003-logical-folder-abstraction.md)). The Trash mailbox is defined by the server, its name varies by provider and language (`INBOX.Trash`, `[Gmail]/Trash`, `[Google Mail]/Trash`), and servers can identify it with the RFC 6154 `\Trash` mailbox attribute. Both production servers advertise it:

```text
(\HasNoChildren \Trash) "/" "[Gmail]/Trash"
(\HasNoChildren \UnMarked \Trash) "." INBOX.Trash
```

## Decision

Keep `move`, `trash`, and `delete` as distinct actions.

- `move: <folder_key>` moves the message to the mailbox mapped by that logical folder key, as in ADR 003.
- `trash: true` applies the provider's Trash operation to the account's Trash mailbox.
- `delete: true` keeps the existing semantics on every provider: `UID STORE +FLAGS (\Deleted)` and a single `EXPUNGE` after the loop.

Treat Trash as a reserved semantic role, not an ordinary logical folder. Resolve it once per account run, only when a `trash` action is reached:

1. the account's explicit `trash` folder mapping, if present;
2. otherwise the single mailbox the server advertises with the `\Trash` attribute in a `LIST` response;
3. otherwise no Trash mailbox: the message is skipped and left unmodified.

Configuration always overrides the server. Discovery uses only `imaplib`'s public `list()` and reuses the advertised name exactly as sent. No name is guessed: a mailbox called `Trash` without the attribute is not used, and a missing, failed, or ambiguous (more than one) result, or a name that cannot be quoted safely, leaves Trash unresolved. Ordinary logical folders are never discovered.

Detect Gmail by the `X-GM-EXT-1` capability. Use a small Gmail branch inside the shared move path rather than a separate handler; `move` and `trash` both use it:

```text
generic IMAP                      Gmail
UID COPY <uid> "<mailbox>"        UID COPY <uid> "<mailbox>"
UID STORE <uid> +FLAGS (\Deleted) UID STORE <uid> -X-GM-LABELS (\Inbox)
EXPUNGE (after the loop)
```

On Gmail, `COPY` adds the destination label and removing `\Inbox` takes the message out of `INBOX` while its other labels remain. Gmail moves and trashes never set `\Deleted` and never require `EXPUNGE`. For `trash`, Gmail's own Trash semantics apply once the message is in the Trash mailbox; the `\Inbox` removal is kept so leaving `INBOX` does not depend on that.

Every message-level operation uses the message UID, as required by [ADR 005](005-uid-based-message-identification.md).

Fail safely:

- A missing folder mapping for `move`, or an unresolved Trash mailbox for `trash`, skips the message.
- A failed `COPY` skips the message before anything is removed.
- A failed `\Inbox` label removal is logged; the message stays in `INBOX` and is never marked `\Deleted` as a fallback.
- If the capability query fails, the provider is unknown and `move` and `trash` are skipped rather than processed with generic semantics. `delete` is unaffected.
- `EXPUNGE` runs only when the run marked at least one message `\Deleted`.

If a rule specifies more than one of these actions, the least destructive wins: `move`, then `trash`, then `delete`. The ignored actions are logged. `mark_read` remains independent and is applied before the copy.

## Consequences

- Gmail moves no longer depend on the account's Auto-Expunge or expunge-disposition settings.
- Other labels on a Gmail message survive a move.
- A rule can send a message to Trash on any provider without changing what `delete` means.
- `delete` on Gmail still follows the account's settings and usually archives; rules that intend Trash should use `trash`.
- Accounts on servers that advertise `\Trash` need no Trash configuration, independent of provider or language. A `trash` mapping remains available as an override, and is required only where the server does not advertise the attribute.
- ADR 003 is unchanged: rules still name no physical mailboxes, and ordinary logical folders are resolved only through configuration. Trash is the one role the server may supply.
- An account run that reaches a `trash` action without a `trash` mapping issues one additional `LIST`.
- Gmail accounts no longer receive a mailbox-wide `EXPUNGE` unless a `delete` rule ran. Generic accounts no longer receive one on runs that marked nothing `\Deleted`.
- A capability-query failure temporarily stops `move` and `trash` for that run; affected messages remain unseen in `INBOX` and are processed on a later run.

## Notes

On the command level, Gmail `trash` and Gmail `move` share one sequence; what differs is Gmail's treatment of its Trash label (the message leaves label views and is purged after 30 days). The separate `trash` action is where a Gmail-specific operation belongs if one is ever needed.

Alternatives not adopted:

- Requiring a `trash` mapping for every account: treats a server-defined role as an owner-organized folder and repeats provider- and language-specific names in every account's configuration.
- Built-in Trash names per provider (for example `[Gmail]/Trash`): Gmail's system folder names vary by region and language, and generic servers have no common name.
- `UID MOVE`: supported by Gmail, but it would introduce a third removal mechanism without improving on the explicit `X-GM-LABELS` operation, and changing generic servers to it is outside this decision.
- `UID STORE +X-GM-LABELS (\Trash)`: avoids a mailbox name, but is not described in Google's extension documentation and would replace the verified Gmail operation.
- `LIST ... RETURN (SPECIAL-USE)`: requires LIST-EXTENDED and private `imaplib` internals. Both production servers include `\Trash` in a plain `LIST`.

The decision was verified through automated tests using a fake IMAP connection that returns responses in the shapes `imaplib` returns and asserts the exact IMAP command sequences for generic and Gmail `move`, `trash`, and `delete`, Trash resolution, and the failure paths above; and through live testing against the Gmail and non-Gmail production accounts, including Gmail `move`, `trash` through both a configured `trash` mapping and the server-advertised `\Trash` mailbox, and the generic `move` and `delete` behavior.
