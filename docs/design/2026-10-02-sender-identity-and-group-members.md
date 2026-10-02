# Sender identity and group members in the JSON

Date: 2026-10-02. Status: approved design, not yet built.

## Why

The JSON does not say who sent a group message. On a received group message,
`sender` holds one of two things: the name of the owner's one-to-one chat with
the sender, or the part of the sender's WhatsApp id before the `@`. A reader
gets a name or a number, never both. It cannot tell the digits of a phone
number from the digits of an internal `@lid` id. No chat lists its members.

`sender_jid`, added on 2026-10-02, keeps the sender's id as the database stores
it. That separates phone ids from `@lid` ids. It does not yet give the phone
number behind an `@lid` id, any name, or the members of a group.

WhatsApp's own database holds all of it. This design adds it to the JSON.

Measured on one iPhone backup with release 0.13.0: 128,386 messages, 95 chats,
42 groups, 17,624 received group messages.

| `sender` holds | Messages |
|---|---|
| A name | 9,862 |
| Digits of a phone number | 4,281 |
| Digits of an `@lid` id | 1,427, from 45 senders |
| Nothing: the message has no group member row | 2,054 |

Every count in this document comes from that backup. No message text, name or
phone number from it is recorded anywhere in this repository.

## Rules

1. The fork writes what the backup holds and guesses nothing. A fact the backup
   lacks is `null`, never a made-up value.
2. Changes to the JSON are additive. `sender`, every other existing field, the
   HTML output and the command line stay as upstream has them.
3. Every field exists on both platforms, iPhone and Android.
4. A pull request goes to this repository's `main`. Nothing is sent upstream.

## Decisions

| Question | Decision |
|---|---|
| What `sender_jid` holds for a sender stored under an `@lid` id | The phone id, whenever the backup maps the `@lid` id to one. `sender_lid` keeps the `@lid` id. |
| How a name is carried | Two fields, by who chose the name: `sender_contact_name` and `sender_push_name`. The reader decides which to prefer. |
| Which people a group's member list holds | Every person with a member row in the backup, each with an `active` flag. |
| Group messages with no member row | Left without a sender id. The backup cannot attribute them. |
| Android | Built with iPhone in the same pull requests, checked by unit tests until a real Android backup is available. |
| Reading the fields in Message Crate | Out of this design. It is messagecrate/message-crate#1092. |

## Terms

- **JID**: WhatsApp's id for a person or a group, such as
  `85212345678@s.whatsapp.net` for a person or `…@g.us` for a group.
- **Phone id**: a JID ending in `@s.whatsapp.net`. The part before the `@` is
  the person's full international phone number.
- **`@lid` id**: an internal id ending in `@lid`. The part before the `@` is
  13 to 15 digits and is not a phone number.
- **Contact name**: the name the owner of the phone gave a person in the
  address book.
- **Push name**: the name a person typed into their own WhatsApp profile.
  WhatsApp shows it after a `~` when the person is not in the reader's address
  book. The person can change it at any time.

## The fields

### On every message

Each is `null` unless the message is a received group message.

| Field | Holds |
|---|---|
| `sender_jid` | The sender's phone id, whenever the backup can supply one. It is an `@lid` id only when the backup has no mapping for it. `null` when the backup names no sender. |
| `sender_lid` | The sender's `@lid` id, when the backup stores the sender under one. Otherwise `null`. |
| `sender_contact_name` | The sender's contact name, or `null`. |
| `sender_push_name` | The sender's current push name from the profile table, or `null`. |

On iPhone this changes what `sender_jid` holds today for a sender stored under
an `@lid` id: the phone id replaces the `@lid` id, which moves to `sender_lid`.
Android already resolves the id this way when its database has a `jid_map`
table.

### On every chat

| Field | Holds |
|---|---|
| `members` | On a group, a list with one entry per person who has a member row in the backup. On any other chat, `null`. |

### Each entry of `members`

| Field | Holds |
|---|---|
| `jid` | As `sender_jid`: the phone id when the backup can supply one, else the `@lid` id. |
| `lid` | As `sender_lid`. |
| `contact_name` | The member's contact name, or `null`. |
| `push_name` | The member's current push name from the profile table, or `null`. |
| `active` | `true` when the person was in the group when the backup was made. |
| `admin` | `true` when the person was an admin of the group. |

A backup can hold two member rows for one person in one group: one under the
`@lid` id and one under the phone id. They become one entry. The entry is
`active` when either row is, and `admin` when either row is.

A sender who has a member row appears in `members` with the same `jid` as on
their messages, so a reader can match the two by `jid`.

The owner of the phone is not in `members`. The backup has no member row for
the owner.

## Where each fact comes from

### iPhone

Measured on the backup.

| Fact | Source |
|---|---|
| The sender's stored id | `ZWAGROUPMEMBER.ZMEMBERJID`, through `ZWAMESSAGE.ZGROUPMEMBER`. |
| The phone id behind an `@lid` id | `LID.sqlite`, table `ZWAZACCOUNT`. It maps all 45 `@lid` senders and 465 of the 467 `@lid` member rows. |
| Contact name | `ZWAGROUPMEMBER.ZCONTACTNAME`, else `ZWAGROUPMEMBER.ZFIRSTNAME`. |
| Push name | `ZWAPROFILEPUSHNAME.ZPUSHNAME`, looked up by the stored id and then by the phone id. |
| Members | Every `ZWAGROUPMEMBER` row of the chat, with `ZISACTIVE` and `ZISADMIN`. In 447 cases two rows of one group are the same person. |

- The tool extracts each database from the backup by its hashed file name.
  `LID.sqlite` is not extracted today and must be added. When the backup has no
  `LID.sqlite`, nothing is mapped and `sender_jid` is the stored id.
- `ZWAMESSAGE.ZPUSHNAME` is not read. Despite its name it is not a name: it is
  base64 text of a protobuf record, and on no received group message does that
  record hold a name.
- `LID.sqlite` is not one of the three databases the tool copies by hashed
  name. The tool does extract every file of WhatsApp's shared folder into the
  media folder, so the file is read from there.

### Android

Taken from the tool's code and the known layout of WhatsApp's Android
databases. Not yet run on a real backup.

| Fact | Source |
|---|---|
| The sender's stored id | `jid.raw_string` through `message.sender_jid_row_id`. On the legacy layout, `remote_resource`. |
| The phone id behind an `@lid` id | The `jid_map` table, which the message query already joins. The query also selects the unmapped id, for `sender_lid`. |
| Contact name | `wa.db`, `wa_contacts.display_name`. |
| Push name | `wa.db`, `wa_contacts.wa_name`. Android keeps no copy on the message. |
| Members | `group_participant_user`. On the legacy layout, `group_participants`. Admin comes from the table's rank column. |

Whether the owner of the phone has a row in the Android member table is not
known; it is checked when a real Android backup is available.

Android keeps only the current members of a group. Every entry is
`active: true`, and a sender who has left the group is on their messages and
not in `members`. This is a real difference from iPhone.

### What the backup cannot supply

2,054 received group messages have no group member row.

- 2,014 of them date from 2016 to 2018, and 1,903 are in one group.
- Each names only the group itself as the sender, and none has a push name
  that can be read. Nothing in the backup says who wrote them. They keep
  `sender_jid` and every sender name as `null`.
- 82 are system messages, which have no author.

## Code shape

- One function per platform turns a stored id into the four sender values:
  phone id, `@lid` id, contact name, push name. The message code and the member
  code both call it, so a person has the same `jid` in both places.
- The id mapping and the name tables are loaded once into dictionaries before
  the message loop. They are not joined into the message query on iPhone, so
  the existing query and everything the HTML output reads stay untouched.
- `Message` and `ChatStore` gain the new attributes with a default of `None`.
  `to_json` writes every attribute, and `from_json` reads back any attribute
  the class has, so an incremental merge keeps the new fields.

## Order of work

Each step is one pull request to `main`.

| Step | Change |
|---|---|
| 1 | `sender_jid` becomes the phone id through `LID.sqlite` on iPhone. `sender_lid` is added on both platforms. |
| 2 | `sender_contact_name` and `sender_push_name` on both platforms. |
| 3 | `members` on each group chat, on both platforms. |

## Checks

**Unit tests.** Written first and seen to fail. Each test builds a small
database in memory with made-up rows, so no real data enters the repository.
Android is tested the same way, over the tables the tool's code reads.

**The iPhone backup.** Each step is run over the backup and its counts are
compared with the figures below. The run is read only, works on a scratch view
of the backup, records counts only, and deletes its scratch files.

| Step | Must hold on the backup |
|---|---|
| 1 | 1,427 received group messages move from an `@lid` id to a phone id in `sender_jid`, and carry the `@lid` id in `sender_lid`. No `sender_jid` is an `@lid` id. 15,570 messages have a `sender_jid`, as before. |
| 2 | Of the 15,570 messages with a `sender_jid`, 7,468 have a `sender_push_name` and 981 have a `sender_contact_name`. Of the 5,708 messages whose `sender` is digits, at least 2,696 have a `sender_push_name`. The 2,054 messages with no member row have neither name. |
| 3 | The 1,318 member rows become one entry per person in each group, with the 447 doubled persons merged; the check script computes the expected entries from the database. Every `sender_jid` in a group is the `jid` of an entry in that group's `members`. |

**Nothing existing changes.** For each step, every field that existed before
the step is identical between the old and the new `result.json`, across all
128,386 messages. `sender_jid` in step 1 is the one stated exception.

**Android.** Unit tests only, until a real Android backup is available. The
same counts-only run is then made and its counts are added to this document.
A backup made before `@lid` ids existed checks the names, the members and the
legacy sender path. It does not check the `jid_map` mapping.

## Errors

- A table or file that a new field needs is absent: the fields that depend on
  it are `null`, the run continues, and one log line names what was absent.

## Not in this design

- Reactions on an iPhone backup: #4.
- iPhone media that is not found: #5.
- Polls: #6.
- The text of group actions: #7.
- Releasing this fork, and Message Crate downloading a pinned release of it:
  messagecrate/message-crate#1053.
- Reading the new fields in Message Crate: messagecrate/message-crate#1092.
