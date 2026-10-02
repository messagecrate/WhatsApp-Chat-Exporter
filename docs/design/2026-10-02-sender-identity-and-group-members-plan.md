# Sender Identity and Group Members Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The JSON says who sent each received group message (phone id, `@lid` id, contact name, push name) and lists the members of each group.

**Architecture:** A new module, `Whatsapp_Chat_Exporter/identity.py`, turns a stored WhatsApp id into an `Identity` (phone id, `@lid` id, contact name, push name) from dictionaries loaded once per run. The iPhone and Android handlers build an `IdentityResolver`, call it for each received group message, and call it again for each group member row, so a person has the same `jid` in both places. `Message` and `ChatStore` gain attributes; `to_json` already writes every attribute.

**Tech Stack:** Python 3.10+, `sqlite3` from the standard library, pytest.

**Spec:** `docs/design/2026-10-02-sender-identity-and-group-members.md`. Read it first.

## Global Constraints

- The fork writes what the backup holds and guesses nothing. A fact the backup lacks is `null`, never a made-up value.
- Changes to the JSON are additive. `sender`, every other existing field, the HTML output and the command line stay as they are. No existing SQL column is removed or renamed.
- Every field exists on both platforms, iPhone and Android.
- A pull request goes to `messagecrate/WhatsApp-Chat-Exporter`, branch `main`. Create it with `gh pr create --repo messagecrate/WhatsApp-Chat-Exporter --base main`; without `--repo`, `gh` targets the upstream repository. Nothing is ever sent upstream.
- No real message text, name or phone number enters the repository. Tests use made-up rows. Example ids in tests use the made-up number `85212345678`.
- `.handoff/` is excluded from git and holds the measuring scripts. Never commit it.
- A table or file that a new field needs is absent: the fields that depend on it are `null`, the run continues, and one log line names what was absent.
- Follow PEP 8. Use spaces. Do not reformat code a task does not change.
- Three pull requests: Tasks 1 to 4 are the first, Tasks 5 to 7 the second, Tasks 8 to 10 the third. Each branch is cut from `main` after the previous pull request is merged.

## Setup, once

```bash
cd /home/mbeisser/repo/WhatsApp-Chat-Exporter
uv venv .venv
uv pip install -p .venv/bin/python -e . pytest pycryptodome javaobj-py3 vobject
.venv/bin/python -m pytest -q
```

Expected: every test passes except four that fail on `main` before any change: three `TestDetermineDay` tests in `tests/test_utility.py` (time zone) and `tests/test_nuitka_binary.py::test_nuitka_binary` (needs a `python` command). "All tests pass" below means "only those four fail".

## File Structure

| File | Responsibility |
|---|---|
| `Whatsapp_Chat_Exporter/identity.py` (new) | `Identity`, `IdentityResolver`, `phone_jid`, `row_value`, `member_entry`, `merge_members`. No database access, no platform knowledge. |
| `Whatsapp_Chat_Exporter/data_model.py` | `Message` gains `sender_lid`, `sender_contact_name`, `sender_push_name`. `ChatStore` gains `members`. |
| `Whatsapp_Chat_Exporter/ios_handler.py` | Loads the `@lid` map and push names, resolves senders, builds members. |
| `Whatsapp_Chat_Exporter/android_handler.py` | Loads names from `wa.db`, resolves senders, builds members. |
| `tests/test_identity.py` (new) | The resolver and the member helpers. |
| `tests/test_ios_identity.py` (new) | iPhone loading, senders and members, over in-memory databases. |
| `tests/test_android_identity.py` (new) | Android names, senders and members, over in-memory databases. |
| `.handoff/measure-identity/check_identity.py` (new, not committed) | Counts on the real backup. |

---

### Task 1: The identity module

**Files:**
- Create: `Whatsapp_Chat_Exporter/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Produces: `Identity(jid, lid, contact_name, push_name)`; `IdentityResolver(lid_to_phone=None, contact_names=None, push_names=None).resolve(stored_jid, mapped_jid=None, contact_name=None) -> Identity`; `phone_jid(number) -> Optional[str]`; `row_value(row, key)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identity.py`:

```python
import sqlite3

from Whatsapp_Chat_Exporter.identity import Identity, IdentityResolver, phone_jid, row_value

PHONE = "85212345678@s.whatsapp.net"
LID = "123456789012345@lid"


def test_phone_id_resolves_to_itself():
    assert IdentityResolver().resolve(PHONE) == Identity(PHONE, None, None, None)


def test_lid_with_a_mapping_resolves_to_the_phone_id_and_keeps_the_lid():
    resolver = IdentityResolver(lid_to_phone={LID: PHONE})
    assert resolver.resolve(LID) == Identity(PHONE, LID, None, None)


def test_lid_without_a_mapping_stays_a_lid():
    assert IdentityResolver().resolve(LID) == Identity(LID, LID, None, None)


def test_a_mapped_id_from_the_caller_wins():
    assert IdentityResolver().resolve(LID, mapped_jid=PHONE) == Identity(PHONE, LID, None, None)


def test_no_stored_id_resolves_to_nothing():
    assert IdentityResolver().resolve(None) == Identity(None, None, None, None)
    assert IdentityResolver().resolve("") == Identity(None, None, None, None)


def test_names_are_found_by_the_stored_id_or_the_phone_id():
    resolver = IdentityResolver(
        lid_to_phone={LID: PHONE},
        contact_names={PHONE: "Ana Example"},
        push_names={LID: "ana"},
    )
    assert resolver.resolve(LID) == Identity(PHONE, LID, "Ana Example", "ana")


def test_a_contact_name_from_the_caller_wins():
    resolver = IdentityResolver(contact_names={PHONE: "From Table"})
    assert resolver.resolve(PHONE, contact_name="From Row").contact_name == "From Row"


def test_an_empty_name_is_no_name():
    resolver = IdentityResolver(contact_names={PHONE: ""}, push_names={PHONE: ""})
    assert resolver.resolve(PHONE) == Identity(PHONE, None, None, None)


def test_phone_jid_keeps_only_digits():
    assert phone_jid("85212345678") == PHONE
    assert phone_jid("+852 1234-5678") == PHONE
    assert phone_jid("") is None
    assert phone_jid(None) is None


def test_row_value_reads_a_dict_and_a_sqlite_row():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT 1 AS a").fetchone()
    assert row_value(row, "a") == 1
    assert row_value(row, "b") is None
    assert row_value({"a": 1}, "a") == 1
    assert row_value({"a": 1}, "b") is None
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'Whatsapp_Chat_Exporter.identity'`.

- [ ] **Step 3: Write the module**

Create `Whatsapp_Chat_Exporter/identity.py`:

```python
"""Who a WhatsApp id belongs to: phone id, @lid id, contact name, push name."""

from typing import Any, Dict, NamedTuple, Optional

PHONE_SUFFIX = "@s.whatsapp.net"
LID_SUFFIX = "@lid"


class Identity(NamedTuple):
    """What a backup knows about one person. A missing fact is None."""
    jid: Optional[str]
    lid: Optional[str]
    contact_name: Optional[str]
    push_name: Optional[str]


NO_IDENTITY = Identity(None, None, None, None)


def phone_jid(number: Optional[str]) -> Optional[str]:
    """Build a phone JID from a phone number, keeping only its digits."""
    digits = "".join(char for char in (number or "") if char.isdigit())
    return digits + PHONE_SUFFIX if digits else None


def row_value(row: Any, key: str) -> Any:
    """Read a column from a dict or a sqlite3.Row; None when it is not there."""
    return row[key] if key in row.keys() else None


def _first_name(names: Dict[str, str], ids) -> Optional[str]:
    for jid in ids:
        name = names.get(jid)
        if name:
            return name
    return None


class IdentityResolver:
    """Turns the id a backup stores for a person into an Identity."""

    def __init__(
            self,
            lid_to_phone: Optional[Dict[str, str]] = None,
            contact_names: Optional[Dict[str, str]] = None,
            push_names: Optional[Dict[str, str]] = None
    ) -> None:
        self.lid_to_phone = lid_to_phone or {}
        self.contact_names = contact_names or {}
        self.push_names = push_names or {}

    def resolve(
            self,
            stored_jid: Optional[str],
            mapped_jid: Optional[str] = None,
            contact_name: Optional[str] = None
    ) -> Identity:
        """Resolve the id as the backup stores it.

        Args:
            stored_jid: The id in the backup, a phone JID or an @lid JID.
            mapped_jid: The phone JID the database itself maps the id to, if any.
            contact_name: A contact name found beside the id, if any.
        """
        if not stored_jid:
            return NO_IDENTITY
        lid = stored_jid if stored_jid.endswith(LID_SUFFIX) else None
        jid = mapped_jid or self.lid_to_phone.get(stored_jid) or stored_jid
        ids = (stored_jid, jid)
        return Identity(
            jid,
            lid,
            contact_name or _first_name(self.contact_names, ids),
            _first_name(self.push_names, ids),
        )
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/sender-lid main
git add Whatsapp_Chat_Exporter/identity.py tests/test_identity.py
git commit -m "feat: resolve a stored WhatsApp id to a phone id, an @lid id and names"
```

---

### Task 2: iPhone, `sender_jid` through `LID.sqlite`, and `sender_lid`

**Files:**
- Modify: `Whatsapp_Chat_Exporter/data_model.py` (`Message.__init__`, after `self.sender_jid = None`)
- Modify: `Whatsapp_Chat_Exporter/ios_handler.py` (imports; new `_load_lid_map` and `_build_identity_resolver`; `messages`; `process_message_data`)
- Modify: `tests/test_incremental_merge.py` (`chat_data_merged`)
- Test: `tests/test_ios_identity.py`

**Interfaces:**
- Consumes: `IdentityResolver`, `phone_jid` from Task 1.
- Produces: `ios_handler._load_lid_map(media_folder) -> Dict[str, str]`; `ios_handler._build_identity_resolver(db, media_folder) -> IdentityResolver`; `process_message_data(message, content, is_group_message, data, message_map, no_reply, identity_resolver=None)`; `Message.sender_lid`.

Background: the tool extracts every file of WhatsApp's shared folder into the media folder (`args.media`, which `messages()` receives as `media_folder`). `LID.sqlite` is therefore at `<media_folder>/LID.sqlite`. Its table `ZWAZACCOUNT` has `ZIDENTIFIER` (an `@lid` JID) and `ZPHONENUMBER` (digits only, or NULL).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ios_identity.py`:

```python
import sqlite3

from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver
from Whatsapp_Chat_Exporter.ios_handler import _load_lid_map, process_message_data

PHONE = "85212345678@s.whatsapp.net"
LID = "123456789012345@lid"


def new_message():
    return Message(from_me=False, timestamp=1463926641, time="10:17", key_id="34B5EF10FBCA37B8E")


def ios_row(**overrides):
    row = {
        "ZISFROMME": 0,
        "ZMEMBERJID": LID,
        "ZMESSAGETYPE": 0,
        "ZMETADATA": None,
        "ZTEXT": "hello",
    }
    row.update(overrides)
    return row


def make_lid_db(folder, rows):
    db = sqlite3.connect(folder / "LID.sqlite")
    db.execute("CREATE TABLE ZWAZACCOUNT (ZIDENTIFIER VARCHAR, ZPHONENUMBER VARCHAR)")
    db.executemany("INSERT INTO ZWAZACCOUNT VALUES (?, ?)", rows)
    db.commit()
    db.close()


class TestLoadLidMap:
    def test_maps_a_lid_to_a_phone_jid(self, tmp_path):
        make_lid_db(tmp_path, [(LID, "85212345678"), ("999@lid", None)])
        assert _load_lid_map(str(tmp_path)) == {LID: PHONE}

    def test_no_file_gives_an_empty_map(self, tmp_path):
        assert _load_lid_map(str(tmp_path)) == {}

    def test_no_media_folder_gives_an_empty_map(self):
        assert _load_lid_map(None) == {}

    def test_a_file_without_the_table_gives_an_empty_map(self, tmp_path):
        sqlite3.connect(tmp_path / "LID.sqlite").close()
        assert _load_lid_map(str(tmp_path)) == {}


class TestSenderLid:
    def test_a_mapped_lid_sender_gets_the_phone_id_and_keeps_the_lid(self):
        message = new_message()
        resolver = IdentityResolver(lid_to_phone={LID: PHONE})
        process_message_data(message, ios_row(), True, ChatCollection(), {}, False, resolver)
        assert message.sender_jid == PHONE
        assert message.sender_lid == LID
        assert message.sender == "123456789012345"

    def test_an_unmapped_lid_sender_keeps_the_lid_in_both(self):
        message = new_message()
        process_message_data(message, ios_row(), True, ChatCollection(), {}, False, IdentityResolver())
        assert message.sender_jid == LID
        assert message.sender_lid == LID

    def test_a_phone_sender_has_no_lid(self):
        message = new_message()
        process_message_data(message, ios_row(ZMEMBERJID=PHONE), True, ChatCollection(), {}, False)
        assert message.sender_jid == PHONE
        assert message.sender_lid is None

    def test_no_lid_without_a_group_member(self):
        message = new_message()
        process_message_data(message, ios_row(ZMEMBERJID=None), True, ChatCollection(), {}, False)
        assert message.sender_jid is None
        assert message.sender_lid is None

    def test_sender_lid_is_written_to_json_and_read_back(self):
        message = new_message()
        message.sender_lid = LID
        assert Message.from_json(message.to_json()).sender_lid == LID
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_ios_identity.py -q`
Expected: collection error, `ImportError: cannot import name '_load_lid_map'`.

- [ ] **Step 3: Add the attribute**

In `Whatsapp_Chat_Exporter/data_model.py`, `Message.__init__`, change

```python
        self.sender_jid = None
```

to

```python
        self.sender_jid = None
        self.sender_lid = None
```

- [ ] **Step 4: Load the map and resolve the sender**

In `Whatsapp_Chat_Exporter/ios_handler.py`, add to the imports at the top:

```python
import sqlite3
from contextlib import closing
from Whatsapp_Chat_Exporter.identity import IdentityResolver, phone_jid
```

Add these two functions above `def messages(`:

```python
def _load_lid_map(media_folder):
    """Map each @lid JID in LID.sqlite to a phone JID. Empty when there is no such file."""
    path = os.path.join(media_folder, "LID.sqlite") if media_folder else None
    if path is None or not os.path.isfile(path):
        logging.info("LID.sqlite was not found; a sender stored under an @lid id keeps that id.")
        return {}
    try:
        with closing(sqlite3.connect(path)) as lid_db:
            rows = lid_db.execute(
                "SELECT ZIDENTIFIER, ZPHONENUMBER FROM ZWAZACCOUNT WHERE ZPHONENUMBER IS NOT NULL"
            ).fetchall()
    except sqlite3.Error as e:
        logging.info(f"LID.sqlite could not be read ({e}); a sender stored under an @lid id keeps that id.")
        return {}
    return {lid: jid for lid, number in rows if (jid := phone_jid(number)) is not None}


def _build_identity_resolver(db, media_folder):
    """Load what the backup knows about people, once per run."""
    return IdentityResolver(lid_to_phone=_load_lid_map(media_folder))
```

In `messages()`, directly after the line `cursor2 = db.cursor()`, add:

```python
    identity_resolver = _build_identity_resolver(db, media_folder)
```

In `messages()`, change the call

```python
            invalid = process_message_data(message, content, is_group_message, data, message_map, no_reply)
```

to

```python
            invalid = process_message_data(
                message, content, is_group_message, data, message_map, no_reply, identity_resolver)
```

Change the signature

```python
def process_message_data(message, content, is_group_message, data, message_map, no_reply):
```

to

```python
def process_message_data(message, content, is_group_message, data, message_map, no_reply,
                         identity_resolver=None):
```

In `process_message_data`, change

```python
        message.sender = name or fallback
        message.sender_jid = content["ZMEMBERJID"]
```

to

```python
        message.sender = name or fallback
        identity = (identity_resolver or IdentityResolver()).resolve(content["ZMEMBERJID"])
        message.sender_jid = identity.jid
        message.sender_lid = identity.lid
```

- [ ] **Step 5: Add the field to the merge test's expected output**

In `tests/test_incremental_merge.py`, inside `chat_data_merged` only, after each of the three lines `"sender_jid": None,` add:

```python
                "sender_lid": None,
```

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (only the four known failures).

- [ ] **Step 7: Commit**

```bash
git add Whatsapp_Chat_Exporter/data_model.py Whatsapp_Chat_Exporter/ios_handler.py tests/test_ios_identity.py tests/test_incremental_merge.py
git commit -m "feat: on iPhone, sender_jid is the phone id behind an @lid id, and sender_lid keeps the @lid id"
```

---

### Task 3: Android, `sender_lid`

**Files:**
- Modify: `Whatsapp_Chat_Exporter/android_handler.py` (imports; `_get_messages_cursor_new`; `_set_group_sender`)
- Test: `tests/test_android_identity.py`

**Interfaces:**
- Consumes: `IdentityResolver`, `row_value` from Task 1; `Message.sender_lid` from Task 2.
- Produces: the message query's new column `group_sender_raw_jid`; `_set_group_sender` reads the resolver from `data.get_system("identity_resolver")`, which Task 6 sets.

Background: when the Android database has a `jid_map` table, the message query already selects `COALESCE(lid_group.raw_string, jid_group.raw_string) as group_sender_jid`: the phone JID when a mapping exists, else the stored JID. The stored JID alone is `jid_group.raw_string`. The legacy layout has no `@lid` ids; its sender is `remote_resource`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_android_identity.py`:

```python
from Whatsapp_Chat_Exporter.android_handler import _set_group_sender
from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message

PHONE = "85212345678@s.whatsapp.net"
LID = "123456789012345@lid"


def new_message():
    return Message(from_me=False, timestamp=1463926641, time="10:17", key_id="34B5EF10FBCA37B8E")


class TestSenderLid:
    def test_a_mapped_lid_sender_gets_the_phone_id_and_keeps_the_lid(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": PHONE, "group_sender_raw_jid": LID}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender_jid == PHONE
        assert message.sender_lid == LID
        assert message.sender == "85212345678"

    def test_an_unmapped_lid_sender_keeps_the_lid_in_both(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": LID, "group_sender_raw_jid": LID}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender_jid == LID
        assert message.sender_lid == LID

    def test_a_phone_sender_has_no_lid(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": PHONE, "group_sender_raw_jid": PHONE}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender_jid == PHONE
        assert message.sender_lid is None

    def test_a_row_without_the_raw_column_still_resolves(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": PHONE}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender_jid == PHONE
        assert message.sender_lid is None

    def test_the_legacy_schema_has_no_lid(self):
        message = new_message()
        _set_group_sender(message, {"remote_resource": PHONE}, ChatCollection(), False)
        assert message.sender_jid == PHONE
        assert message.sender_lid is None
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_android_identity.py -q`
Expected: 2 failed (`test_a_mapped_lid_sender_gets_the_phone_id_and_keeps_the_lid`, `test_an_unmapped_lid_sender_keeps_the_lid_in_both`, both on `sender_lid`), 3 passed.

- [ ] **Step 3: Select the stored id**

In `Whatsapp_Chat_Exporter/android_handler.py`, in `_get_messages_cursor_new`, change the line

```python
                            {group_jid_selection} as group_sender_jid,
```

to

```python
                            {group_jid_selection} as group_sender_jid,
                            jid_group.raw_string as group_sender_raw_jid,
```

Only the query in `_get_messages_cursor_new` changes. The queries in `_get_message_count`, `media`, `vcard` and `calls` stay as they are.

- [ ] **Step 4: Resolve the sender**

Add to the imports at the top of `android_handler.py`:

```python
from Whatsapp_Chat_Exporter.identity import IdentityResolver, row_value
```

Replace the whole of `_set_group_sender` with:

```python
def _set_group_sender(message, content, data, table_message):
    """Set sender name and identity for group messages."""
    name = fallback = None
    stored_jid = mapped_jid = None
    if table_message:
        if content["sender_jid_row_id"] > 0:
            _jid = content["group_sender_jid"]
            mapped_jid = _jid
            stored_jid = row_value(content, "group_sender_raw_jid") or _jid
            if _jid in data:
                name = data.get_chat(_jid).name
            if "@" in _jid:
                fallback = _jid.split('@')[0]
    else:
        if content["remote_resource"] is not None:
            stored_jid = content["remote_resource"]
            if content["remote_resource"] in data:
                name = data.get_chat(content["remote_resource"]).name
            if "@" in content["remote_resource"]:
                fallback = content["remote_resource"].split('@')[0]

    message.sender = name or fallback
    resolver = data.get_system("identity_resolver") or IdentityResolver()
    identity = resolver.resolve(stored_jid, mapped_jid=mapped_jid)
    message.sender_jid = identity.jid
    message.sender_lid = identity.lid
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass. `tests/test_group_sender_jid.py` passes unchanged.

- [ ] **Step 6: Commit**

```bash
git add Whatsapp_Chat_Exporter/android_handler.py tests/test_android_identity.py
git commit -m "feat: on Android, sender_lid keeps the @lid id of a sender"
```

---

### Task 4: Measure the first step on the iPhone backup and open the pull request

**Files:**
- Create: `.handoff/measure-identity/check_identity.py` (not committed)
- Create: `.handoff/measure-identity/counts_step1.txt` (not committed)

**Interfaces:**
- Produces: `check_identity.py <result.json> <backup folder> [<baseline result.json>]`, which Tasks 7 and 10 run again.

Rules for the maintainer's data, all binding:

- `/pool/archive/projects/message-vault/source-data/` is read only. Open SQLite there with `mode=ro&immutable=1`.
- Do not point the tool at the backup's root. Build a view in a scratch folder.
- Kept files hold counts only: no message text, no names, no full phone numbers.
- Delete every scratch file that holds content when the run is done.

- [ ] **Step 1: Write the check script**

Create `.handoff/measure-identity/check_identity.py`:

```python
"""Count-only check of sender identity and members in a result.json.

Usage: check_identity.py <result.json> <backup folder> [<baseline result.json>]
Prints no names, numbers or text.
"""
import json
import sqlite3
import sys
from collections import Counter

MESSAGE_DB = "7c7fba66680ef796b916b067077cc246adacf01d"
LID_DB = "e794f6ffcc3c222535f47684a63d5178da3c4500"
NEW_MESSAGE_FIELDS = {"sender_jid", "sender_lid", "sender_contact_name", "sender_push_name"}
NEW_CHAT_FIELDS = {"members"}


def open_ro(backup, file_id):
    return sqlite3.connect(f"file:{backup}/{file_id[:2]}/{file_id}?mode=ro&immutable=1", uri=True)


def suffix(jid):
    if jid is None:
        return "null"
    return "@" + jid.split("@")[-1] if "@" in jid else "no @"


def is_group(jid):
    return jid.endswith("@g.us")


result = json.load(open(sys.argv[1]))
backup = sys.argv[2]
baseline = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None

total = sum(len(chat["messages"]) for chat in result.values())
print(f"messages={total} chats={len(result)}")

# Senders of received group messages
jid_shape = Counter()
lid_shape = Counter()
names = Counter()
digit_sender_push = 0
no_jid_with_name = 0
outside = Counter()
for chat_jid, chat in result.items():
    for message in chat["messages"].values():
        received_group = is_group(chat_jid) and not message["from_me"]
        if not received_group:
            for field in NEW_MESSAGE_FIELDS:
                if message.get(field) is not None:
                    outside[field] += 1
            continue
        jid_shape[suffix(message.get("sender_jid"))] += 1
        lid_shape[suffix(message.get("sender_lid"))] += 1
        contact, push = message.get("sender_contact_name"), message.get("sender_push_name")
        if message.get("sender_jid") is not None:
            names[("contact" if contact else "-", "push" if push else "-")] += 1
            if push and (message["sender"] or "").isdigit():
                digit_sender_push += 1
        elif contact or push:
            no_jid_with_name += 1
print(f"received group messages by sender_jid shape: {dict(jid_shape)}")
print(f"received group messages by sender_lid shape: {dict(lid_shape)}")
print(f"with a sender_jid, by names: {dict(names)}")
print(f"digit senders with a push name: {digit_sender_push}")
print(f"no sender_jid but a name: {no_jid_with_name}")
print(f"new sender fields set outside received group messages: {dict(outside)}")

# What the database says step 1 must produce
db = open_ro(backup, MESSAGE_DB)
lid_db = open_ro(backup, LID_DB)
lid_map = {
    lid: "".join(ch for ch in number if ch.isdigit()) + "@s.whatsapp.net"
    for lid, number in lid_db.execute(
        "SELECT ZIDENTIFIER, ZPHONENUMBER FROM ZWAZACCOUNT WHERE ZPHONENUMBER IS NOT NULL")
}
expected = Counter()
for (jid,) in db.execute("""
        SELECT g.ZMEMBERJID FROM ZWAMESSAGE m
            JOIN ZWACHATSESSION s ON m.ZCHATSESSION = s.Z_PK
            LEFT JOIN ZWAGROUPMEMBER g ON m.ZGROUPMEMBER = g.Z_PK
        WHERE s.ZCONTACTJID LIKE '%@g.us' AND m.ZISFROMME = 0"""):
    expected[suffix(lid_map.get(jid, jid))] += 1
print(f"expected sender_jid shapes from the database: {dict(expected)}")

# Members
if any("members" in chat for chat in result.values()):
    wrong_kind = sum(1 for jid, chat in result.items() if (chat["members"] is not None) != is_group(jid))
    entries = sum(len(chat["members"]) for chat in result.values() if chat["members"] is not None)
    active = sum(1 for chat in result.values() for m in chat["members"] or [] if m["active"])
    admin = sum(1 for chat in result.values() for m in chat["members"] or [] if m["admin"])
    duplicate = 0
    sender_not_member = 0
    for chat_jid, chat in result.items():
        if chat["members"] is None:
            continue
        jids = [m["jid"] for m in chat["members"]]
        duplicate += len(jids) - len(set(jids))
        member_jids = set(jids)
        for message in chat["messages"].values():
            if not message["from_me"] and message.get("sender_jid") and message["sender_jid"] not in member_jids:
                sender_not_member += 1
    expected_entries = len({
        (session, lid_map.get(jid, jid))
        for session, jid in db.execute("""
            SELECT s.ZCONTACTJID, g.ZMEMBERJID FROM ZWAGROUPMEMBER g
                JOIN ZWACHATSESSION s ON g.ZCHATSESSION = s.Z_PK
            WHERE g.ZMEMBERJID IS NOT NULL""")
        if session in result
    })
    print(f"chats whose members is set on a non-group or null on a group: {wrong_kind}")
    print(f"member entries={entries} expected from the database={expected_entries} "
          f"active={active} admin={admin}")
    print(f"entries that repeat a jid within a group: {duplicate}")
    print(f"received group messages whose sender_jid is not in members: {sender_not_member}")
else:
    print("members: not in this result.json")

# Nothing existing changes
if baseline is not None:
    changed = Counter()
    if set(baseline) != set(result):
        changed["chat set"] += 1
    for chat_jid, old_chat in baseline.items():
        new_chat = result.get(chat_jid, {})
        for key, value in old_chat.items():
            if key == "messages" or key in NEW_CHAT_FIELDS:
                continue
            if new_chat.get(key) != value:
                changed[f"chat.{key}"] += 1
        new_messages = new_chat.get("messages", {})
        if set(old_chat["messages"]) != set(new_messages):
            changed["message set"] += 1
        for message_id, old_message in old_chat["messages"].items():
            new_message = new_messages.get(message_id, {})
            for key, value in old_message.items():
                if key in NEW_MESSAGE_FIELDS:
                    continue
                if new_message.get(key) != value:
                    changed[f"message.{key}"] += 1
    print(f"fields that differ from the baseline (new fields left out): {dict(changed)}")
```

- [ ] **Step 2: Build the scratch view and a baseline from `main`**

`$S` is a scratch folder outside every repository, for example `S=$(mktemp -d)`. A run takes about 5 minutes and writes about 3 GB.

```bash
B=/pool/archive/projects/message-vault/source-data/imessage/iphone_backup
R=/home/mbeisser/repo/WhatsApp-Chat-Exporter
make_view() {  # the tool opens Manifest.db for writing, so each run gets its own copy
  rm -rf "$S/view" && mkdir -p "$S/view"
  for d in $(ls "$B" | grep '^[0-9a-f][0-9a-f]$'); do ln -s "$B/$d" "$S/view/$d"; done
  cp "$B/Manifest.db" "$B/Info.plist" "$B/Manifest.plist" "$B/Status.plist" "$S/view/"
  chmod u+w "$S/view/Manifest.db"
}
run_tool() {  # $1 = git ref, $2 = output name
  git -C "$R" worktree add -q "$S/src-$2" "$1"
  uv venv -q "$S/venv-$2"
  uv pip install -q -p "$S/venv-$2/bin/python" "$S/src-$2"
  make_view && mkdir -p "$S/work-$2"
  (cd "$S/work-$2" && TQDM_DISABLE=1 "$S/venv-$2/bin/wtsexporter" -i --no-html --no-banner \
      -o "$S/work-$2" -j "$S/work-$2/result.json" -b "$S/view" > "$S/run-$2.log" 2>&1)
  echo "exit $?"
}
run_tool main baseline
run_tool feat/sender-lid step1
```

Expected: `exit 0` twice, and `$S/work-baseline/result.json` and `$S/work-step1/result.json` exist.

- [ ] **Step 3: Run the check and compare with the spec**

```bash
"$S/venv-step1/bin/python" "$R/.handoff/measure-identity/check_identity.py" \
    "$S/work-step1/result.json" "$B" "$S/work-baseline/result.json" \
    | tee "$R/.handoff/measure-identity/counts_step1.txt"
```

Expected, all of it:

- `messages=128386`
- `received group messages by sender_jid shape: {'@s.whatsapp.net': 15570, 'null': 2054}`, with no `@lid` key, and equal to the "expected sender_jid shapes from the database" line.
- `received group messages by sender_lid shape` has `'@lid': 1427` and `'null': 16197`.
- `new sender fields set outside received group messages: {}`
- `fields that differ from the baseline (new fields left out): {}`

If a count differs, stop and find the cause before opening the pull request.

- [ ] **Step 4: Delete the scratch files**

```bash
git -C "$R" worktree remove --force "$S/src-step1"
rm -rf "$S/work-step1" "$S/view" "$S/run-step1.log" "$S/run-baseline.log" "$S/venv-step1"
```

Keep `$S/work-baseline/result.json`, `$S/src-baseline` and `$S/venv-baseline` until Task 10 is done; then delete `$S` whole and run `git -C "$R" worktree prune`. If `$S` is lost in between, rebuild the baseline with `run_tool <commit of main before Task 1> baseline`.

- [ ] **Step 5: Open the pull request**

```bash
git push -u origin feat/sender-lid
gh pr create --repo messagecrate/WhatsApp-Chat-Exporter --base main --head feat/sender-lid \
  --title "feat: sender_jid is the phone id behind an @lid id, and sender_lid keeps the @lid id"
```

The body states the change, the tests, and the counts from `counts_step1.txt`, and names the spec. It ends with the attribution line the session's instructions give. Do not merge without the maintainer's word.

---

### Task 5: iPhone, `sender_contact_name` and `sender_push_name`

**Files:**
- Modify: `Whatsapp_Chat_Exporter/data_model.py` (`Message.__init__`)
- Modify: `Whatsapp_Chat_Exporter/ios_handler.py` (new `_load_push_names`; `_build_identity_resolver`; the messages query; `process_message_data`)
- Modify: `tests/test_incremental_merge.py` (`chat_data_merged`)
- Test: `tests/test_ios_identity.py`

**Interfaces:**
- Consumes: `row_value` from Task 1; `_build_identity_resolver` and the `identity_resolver` parameter from Task 2.
- Produces: `ios_handler._load_push_names(db) -> Dict[str, str]`; `Message.sender_contact_name`, `Message.sender_push_name`; the messages query's new columns `ZCONTACTNAME` and `ZFIRSTNAME`.

Background: the push name is in `ZWAPROFILEPUSHNAME` (`ZJID`, `ZPUSHNAME`), one row per person, keyed by a phone JID or an `@lid` JID. `ZWAMESSAGE.ZPUSHNAME` is not a name and is not read. The contact name is on the member row: `ZWAGROUPMEMBER.ZCONTACTNAME`, else `ZFIRSTNAME`.

Branch: `git checkout -b feat/sender-names main`, after the first pull request is merged and `main` is pulled.

- [ ] **Step 1: Write the failing tests**

Change the import lines at the top of `tests/test_ios_identity.py` to:

```python
import sqlite3

from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver
from Whatsapp_Chat_Exporter.ios_handler import _load_lid_map, _load_push_names, process_message_data
```

Append to `tests/test_ios_identity.py`:

```python
def memory_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    return db


class TestLoadPushNames:
    def test_reads_one_name_per_id(self):
        db = memory_db()
        db.execute("CREATE TABLE ZWAPROFILEPUSHNAME (ZJID VARCHAR, ZPUSHNAME VARCHAR)")
        db.executemany("INSERT INTO ZWAPROFILEPUSHNAME VALUES (?, ?)",
                       [(PHONE, "ana"), (LID, "ben"), ("1@s.whatsapp.net", None)])
        assert _load_push_names(db) == {PHONE: "ana", LID: "ben"}

    def test_no_table_gives_no_names(self):
        assert _load_push_names(memory_db()) == {}


class TestSenderNames:
    def test_contact_name_comes_from_the_member_row(self):
        message = new_message()
        row = ios_row(ZMEMBERJID=PHONE, ZCONTACTNAME="Ana Example", ZFIRSTNAME="Ana")
        process_message_data(message, row, True, ChatCollection(), {}, False, IdentityResolver())
        assert message.sender_contact_name == "Ana Example"

    def test_first_name_is_used_when_there_is_no_contact_name(self):
        message = new_message()
        row = ios_row(ZMEMBERJID=PHONE, ZCONTACTNAME=None, ZFIRSTNAME="Ana")
        process_message_data(message, row, True, ChatCollection(), {}, False, IdentityResolver())
        assert message.sender_contact_name == "Ana"

    def test_push_name_is_found_by_the_stored_lid(self):
        message = new_message()
        resolver = IdentityResolver(lid_to_phone={LID: PHONE}, push_names={LID: "ana"})
        process_message_data(message, ios_row(), True, ChatCollection(), {}, False, resolver)
        assert message.sender_push_name == "ana"
        assert message.sender_contact_name is None

    def test_push_name_is_found_by_the_mapped_phone_id(self):
        message = new_message()
        resolver = IdentityResolver(lid_to_phone={LID: PHONE}, push_names={PHONE: "ana"})
        process_message_data(message, ios_row(), True, ChatCollection(), {}, False, resolver)
        assert message.sender_push_name == "ana"

    def test_no_names_without_a_group_member(self):
        message = new_message()
        resolver = IdentityResolver(push_names={PHONE: "ana"})
        process_message_data(message, ios_row(ZMEMBERJID=None), True, ChatCollection(), {}, False, resolver)
        assert message.sender_contact_name is None
        assert message.sender_push_name is None

    def test_names_are_written_to_json_and_read_back(self):
        message = new_message()
        message.sender_contact_name = "Ana Example"
        message.sender_push_name = "ana"
        read = Message.from_json(message.to_json())
        assert read.sender_contact_name == "Ana Example"
        assert read.sender_push_name == "ana"
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_ios_identity.py -q`
Expected: collection error, `ImportError: cannot import name '_load_push_names'`.

- [ ] **Step 3: Add the attributes**

In `Whatsapp_Chat_Exporter/data_model.py`, `Message.__init__`, change

```python
        self.sender_lid = None
```

to

```python
        self.sender_lid = None
        self.sender_contact_name = None
        self.sender_push_name = None
```

- [ ] **Step 4: Load the push names and set the names**

In `Whatsapp_Chat_Exporter/ios_handler.py`, change the import of the identity module to:

```python
from Whatsapp_Chat_Exporter.identity import IdentityResolver, phone_jid, row_value
```

Add above `_build_identity_resolver`:

```python
def _load_push_names(db):
    """Map each JID in ZWAPROFILEPUSHNAME to its push name. Empty when there is no such table."""
    try:
        rows = db.execute(
            "SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZPUSHNAME IS NOT NULL"
        ).fetchall()
    except sqlite3.Error as e:
        logging.info(f"Push names could not be read ({e}); sender_push_name is left empty.")
        return {}
    return {row[0]: row[1] for row in rows if row[0]}
```

Replace the body of `_build_identity_resolver` so the function reads:

```python
def _build_identity_resolver(db, media_folder):
    """Load what the backup knows about people, once per run."""
    return IdentityResolver(
        lid_to_phone=_load_lid_map(media_folder),
        push_names=_load_push_names(db),
    )
```

In `messages()`, in `messages_query`, change the line

```python
            ZWAGROUPMEMBER.ZMEMBERJID,
```

to

```python
            ZWAGROUPMEMBER.ZMEMBERJID,
            ZWAGROUPMEMBER.ZCONTACTNAME,
            ZWAGROUPMEMBER.ZFIRSTNAME,
```

In `process_message_data`, change

```python
        identity = (identity_resolver or IdentityResolver()).resolve(content["ZMEMBERJID"])
        message.sender_jid = identity.jid
        message.sender_lid = identity.lid
```

to

```python
        identity = (identity_resolver or IdentityResolver()).resolve(
            content["ZMEMBERJID"],
            contact_name=row_value(content, "ZCONTACTNAME") or row_value(content, "ZFIRSTNAME"),
        )
        message.sender_jid = identity.jid
        message.sender_lid = identity.lid
        message.sender_contact_name = identity.contact_name
        message.sender_push_name = identity.push_name
```

- [ ] **Step 5: Add the fields to the merge test's expected output**

In `tests/test_incremental_merge.py`, inside `chat_data_merged` only, after each of the three lines `"sender_lid": None,` add:

```python
                "sender_contact_name": None,
                "sender_push_name": None,
```

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add Whatsapp_Chat_Exporter/data_model.py Whatsapp_Chat_Exporter/ios_handler.py tests/test_ios_identity.py tests/test_incremental_merge.py
git commit -m "feat: on iPhone, a group message carries the sender's contact name and push name"
```

---

### Task 6: Android, `sender_contact_name` and `sender_push_name`

**Files:**
- Modify: `Whatsapp_Chat_Exporter/android_handler.py` (new `_load_identity_names`; `contacts`; `_set_group_sender`)
- Test: `tests/test_android_identity.py`

**Interfaces:**
- Consumes: `IdentityResolver` from Task 1; `Message.sender_contact_name`, `Message.sender_push_name` from Task 5.
- Produces: `android_handler._load_identity_names(db) -> IdentityResolver`; `contacts()` stores it with `data.set_system("identity_resolver", ...)`.

Background: `contacts(db, data, enrich_from_vcards)` receives a connection to `wa.db`. Its table `wa_contacts` has `jid`, `display_name` (the contact name from the phone's address book) and `wa_name` (the push name). `contacts()` runs before `messages()`, and only when `wa.db` exists.

- [ ] **Step 1: Write the failing tests**

Change the import lines at the top of `tests/test_android_identity.py` to:

```python
import sqlite3

from Whatsapp_Chat_Exporter.android_handler import _load_identity_names, _set_group_sender, contacts
from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver
```

Append to `tests/test_android_identity.py`:

```python
def wa_db(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE wa_contacts (jid TEXT, display_name TEXT, wa_name TEXT, status TEXT)")
    db.executemany("INSERT INTO wa_contacts VALUES (?, ?, ?, NULL)", rows)
    return db


class TestLoadIdentityNames:
    def test_reads_contact_names_and_push_names(self):
        resolver = _load_identity_names(wa_db([(PHONE, "Ana Example", "ana"), ("1@s.whatsapp.net", None, "ben")]))
        assert resolver.contact_names == {PHONE: "Ana Example"}
        assert resolver.push_names == {PHONE: "ana", "1@s.whatsapp.net": "ben"}

    def test_no_table_gives_no_names(self):
        resolver = _load_identity_names(sqlite3.connect(":memory:"))
        assert resolver.contact_names == {}
        assert resolver.push_names == {}

    def test_contacts_stores_the_resolver(self):
        data = ChatCollection()
        contacts(wa_db([(PHONE, "Ana Example", "ana")]), data, None)
        assert data.get_system("identity_resolver").push_names == {PHONE: "ana"}


class TestSenderNames:
    def test_names_come_from_the_resolver(self):
        message = new_message()
        data = ChatCollection()
        data.set_system("identity_resolver", IdentityResolver(
            contact_names={PHONE: "Ana Example"}, push_names={PHONE: "ana"}))
        content = {"sender_jid_row_id": 7, "group_sender_jid": PHONE, "group_sender_raw_jid": LID}
        _set_group_sender(message, content, data, True)
        assert message.sender_contact_name == "Ana Example"
        assert message.sender_push_name == "ana"

    def test_no_names_without_a_contact_database(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": PHONE, "group_sender_raw_jid": PHONE}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender_contact_name is None
        assert message.sender_push_name is None

    def test_names_on_the_legacy_schema(self):
        message = new_message()
        data = ChatCollection()
        data.set_system("identity_resolver", IdentityResolver(push_names={PHONE: "ana"}))
        _set_group_sender(message, {"remote_resource": PHONE}, data, False)
        assert message.sender_push_name == "ana"
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_android_identity.py -q`
Expected: collection error, `ImportError: cannot import name '_load_identity_names'`.

- [ ] **Step 3: Load the names**

In `Whatsapp_Chat_Exporter/android_handler.py`, add above `def contacts(`:

```python
def _load_identity_names(db):
    """Read contact names and push names from wa.db into an IdentityResolver."""
    contact_names = {}
    push_names = {}
    try:
        rows = db.execute("SELECT jid, display_name, wa_name FROM wa_contacts").fetchall()
    except sqlite3.Error as e:
        logging.info(f"Contact names could not be read ({e}); sender names are left empty.")
        rows = []
    for jid, display_name, wa_name in rows:
        if not jid:
            continue
        if display_name:
            contact_names[jid] = display_name
        if wa_name:
            push_names[jid] = wa_name
    return IdentityResolver(contact_names=contact_names, push_names=push_names)
```

In `contacts()`, directly after the line `c = db.cursor()`, add:

```python
    data.set_system("identity_resolver", _load_identity_names(db))
```

- [ ] **Step 4: Set the names**

In `_set_group_sender`, change

```python
    message.sender_jid = identity.jid
    message.sender_lid = identity.lid
```

to

```python
    message.sender_jid = identity.jid
    message.sender_lid = identity.lid
    message.sender_contact_name = identity.contact_name
    message.sender_push_name = identity.push_name
```

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add Whatsapp_Chat_Exporter/android_handler.py tests/test_android_identity.py
git commit -m "feat: on Android, a group message carries the sender's contact name and push name"
```

---

### Task 7: Measure the second step and open the pull request

**Files:**
- Create: `.handoff/measure-identity/counts_step2.txt` (not committed)

**Interfaces:**
- Consumes: `check_identity.py`, `make_view`, `run_tool` and the baseline from Task 4.

- [ ] **Step 1: Run the tool and the check**

```bash
run_tool feat/sender-names step2
"$S/venv-step2/bin/python" "$R/.handoff/measure-identity/check_identity.py" \
    "$S/work-step2/result.json" "$B" "$S/work-baseline/result.json" \
    | tee "$R/.handoff/measure-identity/counts_step2.txt"
```

Expected, all of it:

- The `sender_jid` and `sender_lid` lines are the same as in `counts_step1.txt`.
- `with a sender_jid, by names`: the counts whose second part is `push` add up to 7,468, and the counts whose first part is `contact` add up to 981.
- `digit senders with a push name` is 2,696 or more.
- `no sender_jid but a name: 0`
- `new sender fields set outside received group messages: {}`
- `fields that differ from the baseline (new fields left out): {}`

If a count differs, stop and find the cause before opening the pull request.

- [ ] **Step 2: Delete the scratch files**

```bash
git -C "$R" worktree remove --force "$S/src-step2"
rm -rf "$S/work-step2" "$S/view" "$S/run-step2.log" "$S/venv-step2"
```

- [ ] **Step 3: Open the pull request**

```bash
git push -u origin feat/sender-names
gh pr create --repo messagecrate/WhatsApp-Chat-Exporter --base main --head feat/sender-names \
  --title "feat: a group message carries the sender's contact name and push name"
```

The body states the change, the tests, and the counts from `counts_step2.txt`, and names the spec. Do not merge without the maintainer's word.

---

### Task 8: The member list in the data model, and the member helpers

**Files:**
- Modify: `Whatsapp_Chat_Exporter/data_model.py` (`ChatStore.__init__`, `ChatStore.merge_with`)
- Modify: `Whatsapp_Chat_Exporter/identity.py` (new `member_entry`, `merge_members`)
- Modify: `tests/test_incremental_merge.py` (`chat_data_merged`)
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: `Identity` from Task 1.
- Produces: `member_entry(identity, active, admin) -> dict` with keys `jid`, `lid`, `contact_name`, `push_name`, `active`, `admin`; `merge_members(entries) -> List[dict]`; `ChatStore.members` (default `None`).

Branch: `git checkout -b feat/group-members main`, after the second pull request is merged and `main` is pulled.

- [ ] **Step 1: Write the failing tests**

Change the import lines at the top of `tests/test_identity.py` to:

```python
import sqlite3

from Whatsapp_Chat_Exporter.data_model import ChatStore
from Whatsapp_Chat_Exporter.identity import (
    Identity, IdentityResolver, member_entry, merge_members, phone_jid, row_value
)
from Whatsapp_Chat_Exporter.utility import Device
```

Append to `tests/test_identity.py`:

```python
def test_member_entry_has_the_six_fields():
    entry = member_entry(Identity(PHONE, LID, "Ana Example", "ana"), True, False)
    assert entry == {
        "jid": PHONE, "lid": LID, "contact_name": "Ana Example", "push_name": "ana",
        "active": True, "admin": False,
    }


def test_two_rows_for_one_person_become_one_entry():
    old_row = member_entry(Identity(PHONE, None, "Ana Example", None), False, True)
    new_row = member_entry(Identity(PHONE, LID, None, "ana"), True, False)
    assert merge_members([old_row, new_row]) == [{
        "jid": PHONE, "lid": LID, "contact_name": "Ana Example", "push_name": "ana",
        "active": True, "admin": True,
    }]


def test_different_people_stay_separate_and_in_order():
    ana = member_entry(Identity(PHONE, None, None, None), True, False)
    ben = member_entry(Identity("85287654321@s.whatsapp.net", None, None, None), True, False)
    assert merge_members([ana, ben]) == [ana, ben]


def test_a_chat_has_no_members_until_they_are_set():
    assert ChatStore(Device.IOS).members is None
    assert ChatStore(Device.IOS).to_json()["members"] is None


def test_members_are_written_to_json_and_read_back():
    chat = ChatStore(Device.IOS, "Group")
    chat.members = [member_entry(Identity(PHONE, None, None, None), True, False)]
    assert ChatStore.from_json(chat.to_json()).members == chat.members


def test_a_merge_takes_the_other_chats_members():
    old = ChatStore(Device.IOS, "Group")
    old.members = [member_entry(Identity(PHONE, None, None, None), False, False)]
    new = ChatStore(Device.IOS, "Group")
    new.members = [member_entry(Identity(PHONE, None, None, None), True, False)]
    old.merge_with(new)
    assert old.members == new.members


def test_a_merge_keeps_members_when_the_other_chat_has_none():
    old = ChatStore(Device.IOS, "Group")
    old.members = [member_entry(Identity(PHONE, None, None, None), True, False)]
    old.merge_with(ChatStore(Device.IOS, "Group"))
    assert old.members is not None
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: collection error, `ImportError: cannot import name 'member_entry'`.

- [ ] **Step 3: Write the helpers**

In `Whatsapp_Chat_Exporter/identity.py`, change the typing import to:

```python
from typing import Any, Dict, Iterable, List, NamedTuple, Optional
```

Append to the file:

```python
def member_entry(identity: Identity, active: bool, admin: bool) -> Dict[str, Any]:
    """One entry of a group chat's `members` list."""
    return {
        "jid": identity.jid,
        "lid": identity.lid,
        "contact_name": identity.contact_name,
        "push_name": identity.push_name,
        "active": bool(active),
        "admin": bool(admin),
    }


def merge_members(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge the entries of one group that share a jid, keeping first-seen order.

    A backup can hold two member rows for one person: one under the @lid id and
    one under the phone id. The merged entry keeps every fact either row has,
    and is active or admin when either row is.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        current = merged.get(entry["jid"])
        if current is None:
            merged[entry["jid"]] = dict(entry)
            continue
        for key in ("lid", "contact_name", "push_name"):
            current[key] = current[key] or entry[key]
        for key in ("active", "admin"):
            current[key] = current[key] or entry[key]
    return list(merged.values())
```

- [ ] **Step 4: Add the attribute and merge it**

In `Whatsapp_Chat_Exporter/data_model.py`, `ChatStore.__init__`, change

```python
        self.media_base = ""
```

to

```python
        self.media_base = ""
        self.members = None
```

In `ChatStore.merge_with`, change

```python
        self.status = other.status or self.status
```

to

```python
        self.status = other.status or self.status
        if other.members is not None:
            self.members = other.members
```

- [ ] **Step 5: Add the field to the merge test's expected output**

In `tests/test_incremental_merge.py`, inside `chat_data_merged` only, each chat has a line `"media_base": "",`. After each one add:

```python
        "members": None,
```

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add Whatsapp_Chat_Exporter/identity.py Whatsapp_Chat_Exporter/data_model.py tests/test_identity.py tests/test_incremental_merge.py
git commit -m "feat: a chat can carry a member list, and two rows for one person merge"
```

---

### Task 9: `members` on iPhone and on Android

**Files:**
- Modify: `Whatsapp_Chat_Exporter/ios_handler.py` (new `_add_group_members`; `messages`)
- Modify: `Whatsapp_Chat_Exporter/android_handler.py` (new `_add_group_members`, `_table_exists`; `messages`)
- Test: `tests/test_ios_identity.py`, `tests/test_android_identity.py`

**Interfaces:**
- Consumes: `member_entry`, `merge_members` from Task 8; `IdentityResolver` from Task 1; `_build_identity_resolver` from Task 2; the `identity_resolver` system value from Task 6.
- Produces: `ios_handler._add_group_members(db, data, identity_resolver)`; `android_handler._add_group_members(db, data)`. Each sets `chat.members` on every group chat in `data`: a list, empty when the backup has no member row for the group. Other chats keep `None`.

Background, iPhone: `ZWAGROUPMEMBER` has `ZCHATSESSION` (the chat's `ZWACHATSESSION.Z_PK`), `ZMEMBERJID`, `ZCONTACTNAME`, `ZFIRSTNAME`, `ZISACTIVE`, `ZISADMIN`. A chat's key in `data` is `ZWACHATSESSION.ZCONTACTJID`. A group's key ends in `@g.us`.

Background, Android: this part is written from the known layout of WhatsApp's Android databases and has not been run on a real backup. The current layout has `group_participant_user` (`group_jid_row_id`, `user_jid_row_id`, `rank`; rank 0 is a member, 1 an admin, 2 the creator), with both ids pointing at `jid._id`. The legacy layout has `group_participants` (`gjid`, `jid`, `admin`), where the owner's own row has an empty `jid`. Android keeps current members only, so every entry is `active: true`.

- [ ] **Step 1: Write the failing iPhone tests**

Change the `ios_handler` import line at the top of `tests/test_ios_identity.py` to:

```python
from Whatsapp_Chat_Exporter.ios_handler import (
    _add_group_members, _load_lid_map, _load_push_names, process_message_data
)
from Whatsapp_Chat_Exporter.data_model import ChatStore
from Whatsapp_Chat_Exporter.utility import Device
```

Append to `tests/test_ios_identity.py`:

```python
GROUP = "85212345678-1463926641@g.us"


def ios_member_db(members):
    db = memory_db()
    db.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR)")
    db.execute("""CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                  ZMEMBERJID VARCHAR, ZCONTACTNAME VARCHAR, ZFIRSTNAME VARCHAR,
                  ZISACTIVE INTEGER, ZISADMIN INTEGER)""")
    db.execute("INSERT INTO ZWACHATSESSION VALUES (1, ?)", (GROUP,))
    db.execute("INSERT INTO ZWACHATSESSION VALUES (2, ?)", (PHONE,))
    db.executemany(
        "INSERT INTO ZWAGROUPMEMBER (ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME, ZISACTIVE, ZISADMIN)"
        " VALUES (1, ?, ?, ?, ?, ?)", members)
    return db


def data_with_group_and_person():
    data = ChatCollection()
    data.add_chat(GROUP, ChatStore(Device.IOS, "Group"))
    data.add_chat(PHONE, ChatStore(Device.IOS, "Ana"))
    return data


class TestGroupMembers:
    def test_a_group_lists_its_member_rows(self):
        db = ios_member_db([(PHONE, "Ana Example", None, 1, 1), ("85287654321@s.whatsapp.net", None, "Ben", 0, 0)])
        data = data_with_group_and_person()
        _add_group_members(db, data, IdentityResolver(push_names={PHONE: "ana"}))
        assert data.get_chat(GROUP).members == [
            {"jid": PHONE, "lid": None, "contact_name": "Ana Example", "push_name": "ana",
             "active": True, "admin": True},
            {"jid": "85287654321@s.whatsapp.net", "lid": None, "contact_name": "Ben", "push_name": None,
             "active": False, "admin": False},
        ]

    def test_a_lid_row_and_a_phone_row_for_one_person_become_one_entry(self):
        db = ios_member_db([(PHONE, None, None, 0, 0), (LID, None, None, 1, 0)])
        data = data_with_group_and_person()
        _add_group_members(db, data, IdentityResolver(lid_to_phone={LID: PHONE}))
        assert data.get_chat(GROUP).members == [
            {"jid": PHONE, "lid": LID, "contact_name": None, "push_name": None,
             "active": True, "admin": False},
        ]

    def test_a_group_without_member_rows_has_an_empty_list(self):
        data = data_with_group_and_person()
        _add_group_members(ios_member_db([]), data, IdentityResolver())
        assert data.get_chat(GROUP).members == []

    def test_a_one_to_one_chat_has_no_member_list(self):
        data = data_with_group_and_person()
        _add_group_members(ios_member_db([(PHONE, None, None, 1, 0)]), data, IdentityResolver())
        assert data.get_chat(PHONE).members is None

    def test_a_group_that_is_not_exported_is_skipped(self):
        data = ChatCollection()
        _add_group_members(ios_member_db([(PHONE, None, None, 1, 0)]), data, IdentityResolver())
        assert len(data) == 0

    def test_no_member_table_leaves_groups_with_an_empty_list(self):
        data = data_with_group_and_person()
        _add_group_members(memory_db(), data, IdentityResolver())
        assert data.get_chat(GROUP).members == []
```

- [ ] **Step 2: Run the iPhone tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_ios_identity.py -q`
Expected: collection error, `ImportError: cannot import name '_add_group_members'`.

- [ ] **Step 3: Build the iPhone member list**

In `Whatsapp_Chat_Exporter/ios_handler.py`, change the import of the identity module to:

```python
from Whatsapp_Chat_Exporter.identity import IdentityResolver, member_entry, merge_members, phone_jid, row_value
```

Add above `def messages(`:

```python
def _add_group_members(db, data, identity_resolver):
    """Set `members` on every group chat: one entry per person with a member row."""
    entries = {}
    try:
        rows = db.execute("""
            SELECT ZWACHATSESSION.ZCONTACTJID,
                ZWAGROUPMEMBER.ZMEMBERJID,
                ZWAGROUPMEMBER.ZCONTACTNAME,
                ZWAGROUPMEMBER.ZFIRSTNAME,
                ZWAGROUPMEMBER.ZISACTIVE,
                ZWAGROUPMEMBER.ZISADMIN
            FROM ZWAGROUPMEMBER
                INNER JOIN ZWACHATSESSION
                    ON ZWAGROUPMEMBER.ZCHATSESSION = ZWACHATSESSION.Z_PK
            WHERE ZWAGROUPMEMBER.ZMEMBERJID IS NOT NULL
            ORDER BY ZWAGROUPMEMBER.Z_PK
        """).fetchall()
    except sqlite3.Error as e:
        logging.info(f"Group members could not be read ({e}); member lists are left empty.")
        rows = []
    for chat_jid, member_jid, contact_name, first_name, is_active, is_admin in rows:
        if data.get_chat(chat_jid) is None:
            continue
        identity = identity_resolver.resolve(member_jid, contact_name=contact_name or first_name)
        entries.setdefault(chat_jid, []).append(member_entry(identity, is_active, is_admin))
    for chat_jid, chat in data.items():
        if chat_jid.endswith("@g.us"):
            chat.members = merge_members(entries.get(chat_jid, []))
```

In `messages()`, the function ends with the line

```python
    logging.info(f"Processed {total_row_number} messages in {convert_time_unit(total_time)}")
```

Directly after that last line of `messages()` (the second `logging.info` in the function, the one about messages, not the one about contacts), add:

```python
    _add_group_members(db, data, identity_resolver)
```

- [ ] **Step 4: Run the iPhone tests and see them pass**

Run: `.venv/bin/python -m pytest tests/test_ios_identity.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing Android tests**

Change the `android_handler` import line at the top of `tests/test_android_identity.py` to:

```python
from Whatsapp_Chat_Exporter.android_handler import (
    _add_group_members, _load_identity_names, _set_group_sender, contacts
)
from Whatsapp_Chat_Exporter.data_model import ChatStore
from Whatsapp_Chat_Exporter.utility import Device
```

Append to `tests/test_android_identity.py`:

```python
GROUP = "85212345678-1463926641@g.us"
BEN = "85287654321@s.whatsapp.net"


def android_db(participants, jid_map=None):
    """jid rows: 1 group, 2 PHONE, 3 LID, 4 BEN. participants: (user_jid_row_id, rank)."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE jid (_id INTEGER PRIMARY KEY, raw_string TEXT)")
    db.executemany("INSERT INTO jid VALUES (?, ?)", [(1, GROUP), (2, PHONE), (3, LID), (4, BEN)])
    db.execute("CREATE TABLE group_participant_user (group_jid_row_id INTEGER, user_jid_row_id INTEGER, rank INTEGER)")
    db.executemany("INSERT INTO group_participant_user VALUES (1, ?, ?)", participants)
    if jid_map is not None:
        db.execute("CREATE TABLE jid_map (lid_row_id INTEGER, jid_row_id INTEGER)")
        db.executemany("INSERT INTO jid_map VALUES (?, ?)", jid_map)
    return db


def legacy_db(participants):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE group_participants (gjid TEXT, jid TEXT, admin INTEGER)")
    db.executemany("INSERT INTO group_participants VALUES (?, ?, ?)", participants)
    return db


def android_data(jid_map_exists):
    data = ChatCollection()
    data.add_chat(GROUP, ChatStore(Device.ANDROID, "Group"))
    data.add_chat(PHONE, ChatStore(Device.ANDROID, "Ana"))
    data.set_system("jid_map_exists", jid_map_exists)
    return data


class TestGroupMembers:
    def test_a_group_lists_its_members(self):
        data = android_data(False)
        data.set_system("identity_resolver", IdentityResolver(
            contact_names={PHONE: "Ana Example"}, push_names={BEN: "ben"}))
        _add_group_members(android_db([(2, 2), (4, 0)]), data)
        assert data.get_chat(GROUP).members == [
            {"jid": PHONE, "lid": None, "contact_name": "Ana Example", "push_name": None,
             "active": True, "admin": True},
            {"jid": BEN, "lid": None, "contact_name": None, "push_name": "ben",
             "active": True, "admin": False},
        ]

    def test_a_lid_member_is_mapped_through_jid_map(self):
        data = android_data(True)
        _add_group_members(android_db([(3, 0)], jid_map=[(3, 2)]), data)
        assert data.get_chat(GROUP).members == [
            {"jid": PHONE, "lid": LID, "contact_name": None, "push_name": None,
             "active": True, "admin": False},
        ]

    def test_a_lid_member_without_a_mapping_stays_a_lid(self):
        data = android_data(True)
        _add_group_members(android_db([(3, 0)], jid_map=[]), data)
        assert data.get_chat(GROUP).members[0]["jid"] == LID
        assert data.get_chat(GROUP).members[0]["lid"] == LID

    def test_the_legacy_table_is_read_and_the_owners_empty_row_is_skipped(self):
        data = android_data(False)
        _add_group_members(legacy_db([(GROUP, "", 1), (GROUP, PHONE, 0)]), data)
        assert data.get_chat(GROUP).members == [
            {"jid": PHONE, "lid": None, "contact_name": None, "push_name": None,
             "active": True, "admin": False},
        ]

    def test_no_member_table_leaves_groups_with_an_empty_list(self):
        data = android_data(False)
        _add_group_members(sqlite3.connect(":memory:"), data)
        assert data.get_chat(GROUP).members == []

    def test_a_one_to_one_chat_has_no_member_list(self):
        data = android_data(False)
        _add_group_members(android_db([(2, 0)]), data)
        assert data.get_chat(PHONE).members is None
```

- [ ] **Step 6: Run the Android tests and see them fail**

Run: `.venv/bin/python -m pytest tests/test_android_identity.py -q`
Expected: collection error, `ImportError: cannot import name '_add_group_members'`.

- [ ] **Step 7: Build the Android member list**

In `Whatsapp_Chat_Exporter/android_handler.py`, change the import of the identity module to:

```python
from Whatsapp_Chat_Exporter.identity import IdentityResolver, member_entry, merge_members, row_value
```

Add above `def messages(`:

```python
def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _group_member_rows(db, jid_map_exists):
    """Rows of (group JID, stored member JID, mapped member JID, is admin)."""
    if _table_exists(db, "group_participant_user"):
        mapped = "COALESCE(phone_jid.raw_string, user_jid.raw_string)" if jid_map_exists else "user_jid.raw_string"
        jid_map_join = """LEFT JOIN jid_map
                            ON jid_map.lid_row_id = group_participant_user.user_jid_row_id
                        LEFT JOIN jid phone_jid
                            ON phone_jid._id = jid_map.jid_row_id""" if jid_map_exists else ""
        return db.execute(f"""
            SELECT group_jid.raw_string,
                user_jid.raw_string,
                {mapped},
                group_participant_user.rank > 0
            FROM group_participant_user
                INNER JOIN jid group_jid
                    ON group_jid._id = group_participant_user.group_jid_row_id
                INNER JOIN jid user_jid
                    ON user_jid._id = group_participant_user.user_jid_row_id
                {jid_map_join}
            ORDER BY group_participant_user.rowid
        """).fetchall()
    if _table_exists(db, "group_participants"):
        return db.execute("""
            SELECT gjid, jid, jid, admin > 0
            FROM group_participants
            WHERE jid IS NOT NULL AND jid != ''
            ORDER BY rowid
        """).fetchall()
    logging.info("No group member table was found; member lists are left empty.")
    return []


def _add_group_members(db, data):
    """Set `members` on every group chat: one entry per member the database lists."""
    resolver = data.get_system("identity_resolver") or IdentityResolver()
    entries = {}
    try:
        rows = _group_member_rows(db, data.get_system("jid_map_exists"))
    except sqlite3.Error as e:
        logging.info(f"Group members could not be read ({e}); member lists are left empty.")
        rows = []
    for group_jid, stored_jid, mapped_jid, is_admin in rows:
        if data.get_chat(group_jid) is None or not stored_jid:
            continue
        identity = resolver.resolve(stored_jid, mapped_jid=mapped_jid)
        entries.setdefault(group_jid, []).append(member_entry(identity, True, is_admin))
    for chat_jid, chat in data.items():
        if chat_jid.endswith("@g.us"):
            chat.members = merge_members(entries.get(chat_jid, []))
```

In `messages()`, change

```python
    _get_reactions(db, data)
```

to

```python
    _get_reactions(db, data)
    _add_group_members(db, data)
```

- [ ] **Step 8: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add Whatsapp_Chat_Exporter/ios_handler.py Whatsapp_Chat_Exporter/android_handler.py tests/test_ios_identity.py tests/test_android_identity.py
git commit -m "feat: each group chat lists its members"
```

---

### Task 10: Measure the third step, open the pull request, and record the results

**Files:**
- Create: `.handoff/measure-identity/counts_step3.txt` (not committed)
- Modify: `docs/design/2026-10-02-sender-identity-and-group-members.md` (status line; a new section "Measured results")

**Interfaces:**
- Consumes: `check_identity.py`, `make_view`, `run_tool` and the baseline from Task 4.

- [ ] **Step 1: Run the tool and the check**

```bash
run_tool feat/group-members step3
"$S/venv-step3/bin/python" "$R/.handoff/measure-identity/check_identity.py" \
    "$S/work-step3/result.json" "$B" "$S/work-baseline/result.json" \
    | tee "$R/.handoff/measure-identity/counts_step3.txt"
```

Expected, all of it:

- The sender lines are the same as in `counts_step2.txt`.
- `chats whose members is set on a non-group or null on a group: 0`
- `member entries` equals `expected from the database`.
- `entries that repeat a jid within a group: 0`
- `received group messages whose sender_jid is not in members: 0`
- `fields that differ from the baseline (new fields left out): {}`

If a count differs, stop and find the cause before opening the pull request.

- [ ] **Step 2: Record the results in the spec**

In `docs/design/2026-10-02-sender-identity-and-group-members.md`, change the status line to `Date: 2026-10-02. Status: built.` and add, before the section "Not in this design", a section "Measured results" with one table: for each of the three steps, the counts from `counts_step1.txt`, `counts_step2.txt` and `counts_step3.txt` that the "Checks" section named, including the number of member entries, how many are active, and how many are admins. Counts only.

```bash
git add docs/design/2026-10-02-sender-identity-and-group-members.md
git commit -m "docs: the measured results of sender identity and group members"
```

- [ ] **Step 3: Delete every scratch file**

```bash
git -C "$R" worktree remove --force "$S/src-step3"
git -C "$R" worktree remove --force "$S/src-baseline"
rm -rf "$S"
git -C "$R" worktree prune
```

- [ ] **Step 4: Open the pull request**

```bash
git push -u origin feat/group-members
gh pr create --repo messagecrate/WhatsApp-Chat-Exporter --base main --head feat/group-members \
  --title "feat: each group chat lists its members"
```

The body states the change, the tests, and the counts from `counts_step3.txt`, and names the spec. Do not merge without the maintainer's word.

- [ ] **Step 5: Tell Message Crate**

Comment on messagecrate/message-crate#1092 that the three field changes are open or merged, with the pull request numbers and the measured counts, so the exporter work can start once the fork has a pinned release (messagecrate/message-crate#1053).

---

## After the plan: the Android backup

When a real Android backup is available, run the tool over it under the same data rules, with a counts-only script of the same shape as `check_identity.py`, reading `msgstore.db` and `wa.db`. Three things are to be confirmed and written into the spec's Android section: the column names of `group_participant_user` or `group_participants`, whether the owner of the phone has a row in the member table, and the counts of each new field.
