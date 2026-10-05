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

    def test_the_legacy_schema_has_no_lid(self):
        message = new_message()
        _set_group_sender(message, {"remote_resource": PHONE}, ChatCollection(), False)
        assert message.sender_jid == PHONE
        assert message.sender_lid is None
