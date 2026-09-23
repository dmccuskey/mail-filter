# Mail Filter Rule Reference

## Match Fields: Quick Reference

| Field family | Checks | `_is` (exact) | `_contains` | `_starts_with` | `_ends_with` |
|---|---|---|---|---|---|
| `to_*` | full addresses in the `To` header | `to_is` | `to_contains` | `to_starts_with` | `to_ends_with` |
| `to_local_*` | part before `@` of each `To` address | `to_local_is` | `to_local_contains` | `to_local_starts_with` | `to_local_ends_with` |
| `from_email_*` | sender's email address | `from_email_is` | `from_email_contains` | `from_email_starts_with` | `from_email_ends_with` |
| `from_name_*` | sender's decoded display name | `from_name_is` | `from_name_contains` | `from_name_starts_with` | `from_name_ends_with` |
| `subject_*` | decoded subject | `subject_is` | `subject_contains` | `subject_starts_with` | `subject_ends_with` |

These 20 names are the only supported match fields. Matching is case-insensitive. Any other name in `match` is an error that stops the run (see [Unknown match fields](#unknown-match-fields)).

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

All match and action fields are optional. A rule with no match fields matches every message.

## Rule Ordering

Rules are evaluated from top to bottom.

The first rule that matches wins.

Later rules are not evaluated for that message.

This makes rule ordering significant.

Specific rules should therefore generally appear before broader prefix or catch-all rules.

## Match Semantics

### Operations

Every match field name is a field family followed by an operation:

| Operation | Meaning | `"shop"` matches | `"shop"` does not match |
|---|---|---|---|
| `_is` | exact match of the whole value | `shop` | `shop-amazon` |
| `_contains` | substring anywhere in the value | `my-shop-amazon` | `sho-p` |
| `_starts_with` | prefix | `shop-amazon` | `my-shop` |
| `_ends_with` | suffix | `my-shop` | `shop-amazon` |

Comparison is case-insensitive: `"Receipt"`, `"receipt"`, and `"RECEIPT"` behave the same, and so do upper- and lower-case message text.

### Different fields: AND

Different match fields in one rule are combined with AND semantics.

For example:

```json5
"match": {
  "to_local_is": "shop-amazon",
  "subject_contains": "receipt"
}
```

means:

```text
a To address has local part "shop-amazon"
AND
subject contains "receipt"
```

### Multiple values: OR

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

AND/OR combination is fixed as described here; it is not configurable.

### Single values

Every match field accepts either a list or a single string. A single string is treated as a one-item list, so these are equivalent:

```json5
"from_email_contains": "amazon.com"
"from_email_contains": ["amazon.com"]
```

A single string is always one value. It is never split into characters or words. To match any of several values, use a list.

An empty list adds no condition.

## Recipient Fields

### Which headers are used

`to_*` and `to_local_*` read addresses from the message's **`To` header only**.

`Cc`, `Delivered-To`, and `X-Original-To` are not used. A message sent to you only as a Cc, or whose address appears only in delivery headers (for example after forwarding), does not match a `to_*` or `to_local_*` rule on that address.

### Multiple addresses

If the `To` header lists several addresses, the field matches when **any one** address matches **any one** value.

For:

```text
To: Alice <alice@example.com>, shop-amazon@aerospace.biz
```

both `"to_local_is": "shop-amazon"` and `"to_is": "alice@example.com"` match.

### Full address vs. local part

`to_*` compares the full address, including the domain. `to_local_*` compares only the part before `@`.

For:

```text
shop-amazon@aerospace.biz
```

| Field | Compares against |
|---|---|
| `to_*` | `shop-amazon@aerospace.biz` |
| `to_local_*` | `shop-amazon` |

Examples:

```json5
"to_is": "payments@aerospace.biz"     // this exact address
"to_ends_with": "@aerospace.biz"      // any address at this domain
"to_local_is": "shop-amazon"          // shop-amazon at any domain
"to_local_starts_with": "shop-"       // shop-amazon, shop-shell, shop-newegg, ...
```

`"to_is": "shop-amazon"` does not match `shop-amazon@aerospace.biz`, because `to_is` needs the whole address. Use `to_local_is` for the local part.

The display name in the `To` header (`Alice` above) is not matched by either family.

## Sender Fields

### `from_email_*`

Compares the sender's email address from the `From` header, such as `noreply@github.com`.

```json5
"from_email_is": "noreply@github.com"
"from_email_ends_with": "@github.com"
"from_email_contains": "amazon.com"
```

### `from_name_*`

Compares the sender's display name, the text before the address in the `From` header, MIME-decoded before matching.

For:

```text
From: GitHub <noreply@github.com>
```

the display name is `GitHub` and the email address is `noreply@github.com`. `"from_name_contains": "github.com"` does not match, because the name does not contain the address.

```json5
"from_name_is": "GitHub"
"from_name_contains": "Fastmail"
```

A message with no display name has an empty name, so it matches no `from_name_*` value.

## Subject Fields

`subject_*` compares the subject after MIME decoding.

```json5
"subject_is": "Your receipt from Fastmail"
"subject_starts_with": "Re:"
"subject_contains": ["receipt", "order confirmation"]
```

`subject_is` must match the whole subject, so `"subject_is": "Hello"` does not match `Hello there`.

## Unknown Match Fields

Before connecting to any account, the filter checks every rule in `rules.local.json5`. If any rule uses a match field not in the quick reference above, the filter logs one error line per unknown field, naming the account, the rule, and the field, then exits with status 1. For example:

```text
[2026-09-23 09:15:02] [gmail-main] ERROR: rule #1 'GitHub mail' uses unknown match field 'from_contains'
[2026-09-23 09:15:02] ERROR: 1 unknown match field(s) in rules.local.json5; no mail processed (supported fields: docs/rule-reference.md)
```

Every unknown field in every account is reported in the same run. No mail is processed in that run. An unknown field is never skipped, because skipping it would drop a condition and let the rule match mail it should not.

There are no aliases. Earlier versions used different names, which are now errors:

| Earlier name | Replacement |
|---|---|
| `to` | `to_local_is` |
| `to_prefix` | `to_local_starts_with` |
| `from_contains` | `from_email_contains` |
| `subject_equals` | `subject_is` |

The earlier `to` and `to_prefix` also read `Cc`, `Delivered-To`, and `X-Original-To`; their replacements read `To` only.

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

- `not_subject_contains`
- `unread_only` matcher
- `age_minutes_gt`
- `mark_unread`
- rule-level `log`

These should not be treated as current capabilities.

Some may become future rule features.

Possible future match features, also not implemented:

- `cc_*` matchers for the `Cc` header
- configurable AND/OR combination of match values

(Exact sender-address matching, once listed here as `from`, is now `from_email_is`.)

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
