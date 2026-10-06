"""group_action and group_action_jid: the text of a group action and the member who acted."""
import json
import logging
import sqlite3

from Whatsapp_Chat_Exporter.android_handler import _process_metadata_message, _set_group_sender
from Whatsapp_Chat_Exporter.data_model import ChatCollection, ChatStore, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver
from Whatsapp_Chat_Exporter.ios_handler import _group_event_type_column, process_message_data
from Whatsapp_Chat_Exporter.utility import Device, JidType

ACTOR = "85212345678@s.whatsapp.net"
OTHER = "85287654321@s.whatsapp.net"
LID = "123456789012345@lid"


def new_message():
    return Message(from_me=False, timestamp=1463926641, time="10:17", key_id="34B5EF10FBCA37B8E")


def ios_row(**overrides):
    row = {
        "ZISFROMME": 0,
        "ZMEMBERJID": ACTOR,
        "ZMESSAGETYPE": 6,
        "ZGROUPEVENTTYPE": None,
        "ZMETADATA": None,
        "ZTEXT": None,
    }
    row.update(overrides)
    return row


def named(jid, name, device=Device.IOS):
    data = ChatCollection()
    data.add_chat(jid, ChatStore(device, name))
    return data


def ios(row, data=None, resolver=None, group=True):
    message = new_message()
    invalid = process_message_data(message, row, group, data or ChatCollection(), {}, False, resolver)
    return message, invalid


class TestIosGroupAction:
    def test_a_rename_with_a_null_author_is_exported_without_a_crash(self):
        text = json.dumps({"author": None, "subject": "Book club"})
        message, invalid = ios(ios_row(ZTEXT=text, ZMEMBERJID=None))
        assert not invalid
        assert message.group_action == "Someone changed the group name to Book club."
        assert message.group_action_jid is None

    def test_a_rename_names_its_author(self):
        text = json.dumps({"author": ACTOR, "subject": "Book club"})
        message, _ = ios(ios_row(ZTEXT=text), named(ACTOR, "Friend"))
        assert message.group_action == "Friend changed the group name to Book club."
        assert message.group_action_jid == ACTOR

    def test_a_json_action_without_a_subject_is_not_called_a_rename(self):
        text = json.dumps({"author": LID, "subject": None, "parent_group_name": "Parents"})
        resolver = IdentityResolver(lid_to_phone={LID: ACTOR})
        message, _ = ios(ios_row(ZTEXT=text, ZMEMBERJID=LID, ZGROUPEVENTTYPE=51), resolver=resolver)
        assert message.group_action == "Unsupported WhatsApp internal message."
        assert message.group_action_jid == ACTOR

    def test_a_plain_rename_names_the_member_who_made_it(self):
        message, _ = ios(ios_row(ZTEXT="Book club", ZGROUPEVENTTYPE=1), named(ACTOR, "Friend"))
        assert message.group_action == "Friend changed the group name to Book club."
        assert message.group_action_jid == ACTOR

    def test_a_join_names_the_member_who_joined(self):
        message, _ = ios(ios_row(ZTEXT=OTHER, ZGROUPEVENTTYPE=2), named(OTHER, "Newcomer"))
        assert message.group_action == "Newcomer joined the group"
        assert message.group_action_jid == OTHER

    def test_a_join_by_an_lid_member_carries_the_phone_id(self):
        resolver = IdentityResolver(lid_to_phone={LID: OTHER})
        message, _ = ios(ios_row(ZTEXT=LID, ZGROUPEVENTTYPE=2), resolver=resolver)
        assert message.group_action == "123456789012345 joined the group"
        assert message.group_action_jid == OTHER

    def test_an_id_under_another_event_type_is_not_called_a_join(self):
        message, _ = ios(ios_row(ZTEXT=OTHER, ZGROUPEVENTTYPE=7))
        assert message.group_action == "Unsupported WhatsApp internal message."
        assert message.group_action_jid is None

    def test_a_leave_names_the_member_who_left(self):
        message, _ = ios(ios_row(ZGROUPEVENTTYPE=3), named(ACTOR, "Friend"))
        assert message.group_action == "Friend left the group"
        assert message.group_action_jid == ACTOR

    def test_a_picture_change_names_the_member_who_made_it(self):
        message, _ = ios(ios_row(ZGROUPEVENTTYPE=4))
        assert message.group_action == "85212345678 changed the group picture"
        assert message.group_action_jid == ACTOR

    def test_the_owner_acting_is_you_and_has_no_id(self):
        message, _ = ios(ios_row(ZGROUPEVENTTYPE=4, ZISFROMME=1, ZMEMBERJID=None))
        assert message.group_action == "You changed the group picture"
        assert message.group_action_jid is None

    def test_an_unknown_event_without_text_has_no_group_action(self):
        message, _ = ios(ios_row(ZGROUPEVENTTYPE=12))
        assert message.group_action is None
        assert message.group_action_jid is None

    def test_the_html_text_stays_as_upstream_main_writes_it(self):
        text = json.dumps({"author": None, "subject": "Book club"})
        message, _ = ios(ios_row(ZTEXT=text))
        assert message.data == f"The group name changed to {text}"
        assert message.meta

    def test_a_number_as_text_is_still_dropped(self):
        _, invalid = ios(ios_row(ZTEXT="12"))
        assert invalid

    def test_a_message_that_is_not_a_group_action_has_neither_field(self):
        message, _ = ios(ios_row(ZMESSAGETYPE=0, ZTEXT="hello"))
        assert message.group_action is None
        assert message.group_action_jid is None

    def test_a_metadata_message_outside_a_group_has_neither_field(self):
        message, _ = ios(ios_row(ZGROUPEVENTTYPE=4), group=False)
        assert message.group_action is None
        assert message.group_action_jid is None


class TestGroupEventTypeColumn:
    def test_the_column_is_read_when_present(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER, ZGROUPEVENTTYPE INTEGER)")
        assert _group_event_type_column(db) == "ZWAMESSAGE.ZGROUPEVENTTYPE"

    def test_a_backup_without_the_column_reads_null_and_says_so(self, caplog):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER)")
        with caplog.at_level(logging.INFO):
            assert _group_event_type_column(db) == "NULL AS ZGROUPEVENTTYPE"
        assert "ZGROUPEVENTTYPE" in caplog.text


def android_content(action_type, **overrides):
    content = {
        "jid_type": JidType.GROUP,
        "key_from_me": 0,
        "sender_jid_row_id": 7,
        "group_sender_jid": ACTOR,
        "group_sender_raw_jid": ACTOR,
        "action_type": action_type,
        "is_me_joined": 0,
        "data": None,
        "thumb_image": None,
        "video_call": None,
        "old_jid": None,
        "new_jid": None,
    }
    content.update(overrides)
    return content


def android(content, data=None):
    message = new_message()
    data = data or ChatCollection()
    if content["jid_type"] == JidType.GROUP and content["key_from_me"] == 0:
        _set_group_sender(message, content, data, True)
    _process_metadata_message(message, content, data, True)
    return message


class TestAndroidGroupAction:
    def test_a_rename_carries_the_text_and_the_actor(self):
        message = android(android_content(1, data="Book club"), named(ACTOR, "Friend", Device.ANDROID))
        assert message.group_action == 'Friend changed the group name to "Book club"'
        assert message.group_action == message.data
        assert message.group_action_jid == ACTOR

    def test_a_join_carries_the_member_who_joined(self):
        message = android(android_content(4))
        assert message.group_action == "85212345678 was added to the group"
        assert message.group_action_jid == ACTOR

    def test_a_leave_carries_the_member_who_left(self):
        message = android(android_content(5))
        assert message.group_action == "85212345678 left the group"
        assert message.group_action_jid == ACTOR

    def test_a_picture_change_carries_the_actor(self):
        message = android(android_content(6))
        assert message.group_action == "85212345678 changed the group icon"
        assert message.group_action_jid == ACTOR

    def test_a_mapped_lid_actor_carries_the_phone_id(self):
        message = android(android_content(5, group_sender_raw_jid=LID))
        assert message.group_action_jid == ACTOR

    def test_the_owner_acting_has_no_id(self):
        message = android(android_content(6, sender_jid_row_id=0, group_sender_jid=None, key_from_me=1))
        assert message.group_action == "You changed the group icon"
        assert message.group_action_jid is None

    def test_a_security_code_change_is_not_a_group_action(self):
        message = android(android_content(18))
        assert message.data is not None
        assert message.group_action is None
        assert message.group_action_jid is None

    def test_a_group_action_outside_a_group_has_neither_field(self):
        message = android(android_content(1, data="x", jid_type=JidType.PM))
        assert message.group_action is None
        assert message.group_action_jid is None

    def test_a_message_that_is_not_a_group_action_has_neither_field(self):
        message = new_message()
        assert message.group_action is None
        assert message.group_action_jid is None
        assert "group_action" in message.to_json()
        assert "group_action_jid" in message.to_json()
