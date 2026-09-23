# Mail Filter Rule Reference

## Rule Structure

A rule has three conceptual parts:

```json5
{
  "name": "Human-readable rule name",

  "match": {
    // conditions
  },

  "do": {
    // actions
  }
}
```

All match and action fields are optional.

## Rule Ordering

Rules are evaluated from top to bottom.

The first rule that matches wins.

Later rules are not evaluated for that message.

This makes rule ordering significant.

Specific rules should therefore generally appear before broader prefix or catch-all rules.

## Match Semantics

Different match fields are combined with AND semantics.

For example:

```json5
"match": {
  "to": ["shop-amazon"],
  "subject_contains": ["receipt"]
}
```

means:

```text
recipient matches shop-amazon
AND
subject contains "receipt"
```

Multiple values within a field use OR semantics.

For example:

```json5
"subject_contains": [
  "receipt",
  "order confirmation",
  "shipment"
]
```

matches if the subject contains any one of those values.

String matching is case-insensitive.

### Single values

Every match field accepts either a list or a single string. A single string is treated as a one-item list, so these are equivalent:

```json5
"from_contains": "amazon.com"
"from_contains": ["amazon.com"]
```

This applies to `to`, `to_prefix`, `from_contains`, `from_name_contains`, `subject_contains`, and `subject_equals`.

A single string is always one value. It is never split into characters or words. To match any of several values, use a list.

## Implemented Match Fields

### `to`

Matches the local part of a recipient address exactly.

For:

```text
shop-amazon@aerospace.biz
```

the local part is:

```text
shop-amazon
```

Example:

```json5
"to": ["shop-amazon"]
```

Recipient information is collected from:

- `To`
- `Cc`
- `Delivered-To`
- `X-Original-To`

### `to_prefix`

Matches the beginning of a recipient's local part.

Example:

```json5
"to_prefix": ["shop-"]
```

matches:

```text
shop-amazon
shop-shell
shop-newegg
```

### `from_contains`

Case-insensitive substring match against the sender's email address.

Example:

```json5
"from_contains": ["amazon.com"]
```

### `from_name_contains`

Case-insensitive substring match against the sender's display name.

Example:

```json5
"from_name_contains": ["Fastmail"]
```

The display name is MIME-decoded before matching.

### `subject_contains`

Case-insensitive substring match against the decoded subject.

Example:

```json5
"subject_contains": [
  "receipt",
  "order confirmation"
]
```

### `subject_equals`

Exact case-insensitive subject comparison.

Example:

```json5
"subject_equals": [
  "Your receipt from Fastmail"
]
```

A single string value is also accepted (see [Single values](#single-values)).

## Implemented Actions

### `move`

Moves a message to a logical folder key.

Example:

```json5
"do": {
  "move": "services_receipts"
}
```

The logical key is resolved through `folders.local.json5`.

The current implementation performs the move as:

```text
COPY
mark original \Deleted
EXPUNGE
```

with the expunge occurring after the account's message-processing loop.

On Gmail, the move is performed as:

```text
UID COPY (adds the destination label)
UID STORE -X-GM-LABELS (\Inbox)
```

Gmail moves do not mark the message `\Deleted` or issue `EXPUNGE`, and the message keeps its other labels.

If the server's capabilities cannot be read, the provider is unknown and the move is skipped (see [ADR 006](decisions/006-gmail-move-and-trash-semantics.md)).

### `trash`

Puts the message in the account's Trash, using the provider's Trash semantics.

The Trash mailbox is resolved per account: the account's optional `trash` folder mapping if present, otherwise the mailbox the server advertises with the IMAP `\Trash` attribute. If neither is available, the message is logged and left untouched. See [Configuration](configuration.md).

Example:

```json5
"do": {
  "trash": true
}
```

On a generic IMAP server, `trash` is performed like `move` to the Trash mailbox (copy, `\Deleted`, expunge).

On Gmail, the message is copied to the Trash mailbox and its `\Inbox` label is removed, without `\Deleted` or `EXPUNGE`. Gmail then applies its own Trash behavior, including permanent removal after 30 days.

`trash` is not the same as `delete`: on Gmail, `delete` usually archives the message rather than trashing it.

### `delete`

Marks the message `\Deleted`.

The message is physically removed when the account is expunged.

On Gmail, the result of an expunge depends on the account's IMAP settings; by default the message is archived (it remains in All Mail). Use `trash` to send a message to Trash.

### `mark_read`

Marks the message `\Seen`.

It can be combined with `move`, `trash`, or `delete`, in which case the message is marked read before it is copied or deleted:

```json5
"do": {
  "move": "travel_scotts_flights",
  "mark_read": true
}
```

It can also be used on its own. The message is marked read and stays in `INBOX`:

```json5
"do": {
  "mark_read": true
}
```

Because the filter only processes unseen messages, a message marked read is not evaluated again on later runs.

## Conflicting Actions

If more than one of `move`, `trash`, and `delete` is specified:

```json5
"do": {
  "move": "somewhere",
  "delete": true
}
```

the least destructive action wins: `move`, then `trash`, then `delete`.

The implementation logs a warning and ignores the lower-precedence actions. `mark_read` is independent and still applies.

## Catch-All

An account can define:

```json5
"catch_all": {
  "move": "catch_all"
}
```

If no normal rule matches, the catch-all action is returned.

If there is no matching rule and no catch-all, the message is left untouched.

## Current Processing Scope

The engine currently searches only for `UNSEEN` messages.

This is a property of the processing loop and should not be confused with an `unread_only` rule matcher.

## Not Currently Implemented

The following appear in historical configuration/documentation but are not currently implemented by the rule engine:

- `from` exact-address matcher
- `not_subject_contains`
- `unread_only` matcher
- `age_minutes_gt`
- `mark_unread`
- rule-level `log`

These should not be treated as current capabilities.

Some may become future rule features.

## Rule Design Principle

The rule system deliberately separates:

```text
match
```

from:

```text
do
```

This keeps classification logic independent from side effects and allows actions to grow without changing the basic rule structure.
