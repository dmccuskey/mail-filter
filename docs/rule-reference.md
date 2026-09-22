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

A string value is also accepted by the current implementation.

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

### `delete`

Marks the message `\Deleted`.

The message is physically removed when the account is expunged.

### `mark_read`

Marks the message `\Seen`.

Example:

```json5
"do": {
  "move": "travel_scotts_flights",
  "mark_read": true
}
```

## Move vs Delete

If both are specified:

```json5
"do": {
  "move": "somewhere",
  "delete": true
}
```

move takes precedence.

The implementation logs a warning and ignores `delete`.

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
