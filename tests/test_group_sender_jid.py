from Whatsapp_Chat_Exporter.android_handler import _set_group_sender
from Whatsapp_Chat_Exporter.data_model import ChatCollection, ChatStore, Message
from Whatsapp_Chat_Exporter.ios_handler import process_message_data
from Whatsapp_Chat_Exporter.utility import Device

MEMBER = "85212345678@s.whatsapp.net"


def new_message():
    return Message(from_me=False, timestamp=1463926641, time="10:17", key_id="34B5EF10FBCA37B8E")


def ios_row(**overrides):
    row = {
        "ZISFROMME": 0,
        "ZMEMBERJID": MEMBER,
        "ZMESSAGETYPE": 0,
        "ZMETADATA": None,
        "ZTEXT": "hello",
    }
    row.update(overrides)
    return row


def collection_with_named_member(device):
    data = ChatCollection()
    data.add_chat(MEMBER, ChatStore(device, "Friend"))
    return data


class TestIosGroupSenderJid:
    def test_sender_jid_is_kept_beside_the_name(self):
        message = new_message()
        process_message_data(message, ios_row(), True, collection_with_named_member(Device.IOS), {}, False)
        assert message.sender == "Friend"
        assert message.sender_jid == MEMBER

    def test_sender_jid_is_kept_when_the_sender_has_no_name(self):
        message = new_message()
        process_message_data(message, ios_row(), True, ChatCollection(), {}, False)
        assert message.sender == "85212345678"
        assert message.sender_jid == MEMBER

    def test_no_sender_jid_without_a_group_member(self):
        message = new_message()
        process_message_data(message, ios_row(ZMEMBERJID=None), True, ChatCollection(), {}, False)
        assert message.sender is None
        assert message.sender_jid is None

    def test_no_sender_jid_on_own_message(self):
        message = new_message()
        process_message_data(message, ios_row(ZISFROMME=1), True, ChatCollection(), {}, False)
        assert message.sender_jid is None

    def test_no_sender_jid_outside_a_group(self):
        message = new_message()
        process_message_data(message, ios_row(), False, ChatCollection(), {}, False)
        assert message.sender_jid is None


class TestAndroidGroupSenderJid:
    def test_sender_jid_is_kept_beside_the_name(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": MEMBER, "group_sender_raw_jid": MEMBER}
        _set_group_sender(message, content, collection_with_named_member(Device.ANDROID), True)
        assert message.sender == "Friend"
        assert message.sender_jid == MEMBER

    def test_sender_jid_is_kept_when_the_sender_has_no_name(self):
        message = new_message()
        content = {"sender_jid_row_id": 7, "group_sender_jid": MEMBER, "group_sender_raw_jid": MEMBER}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender == "85212345678"
        assert message.sender_jid == MEMBER

    def test_no_sender_jid_without_a_sender_row(self):
        message = new_message()
        content = {"sender_jid_row_id": 0, "group_sender_jid": None}
        _set_group_sender(message, content, ChatCollection(), True)
        assert message.sender is None
        assert message.sender_jid is None

    def test_sender_jid_from_the_legacy_schema(self):
        message = new_message()
        _set_group_sender(message, {"remote_resource": MEMBER}, ChatCollection(), False)
        assert message.sender == "85212345678"
        assert message.sender_jid == MEMBER

    def test_no_sender_jid_from_the_legacy_schema_without_a_sender(self):
        message = new_message()
        _set_group_sender(message, {"remote_resource": None}, ChatCollection(), False)
        assert message.sender_jid is None


def test_sender_jid_is_written_to_json_and_read_back():
    message = new_message()
    message.sender = "Friend"
    message.sender_jid = MEMBER
    written = message.to_json()
    assert written["sender_jid"] == MEMBER
    assert Message.from_json(written).sender_jid == MEMBER


def test_sender_jid_is_in_json_when_there_is_none():
    assert new_message().to_json()["sender_jid"] is None
