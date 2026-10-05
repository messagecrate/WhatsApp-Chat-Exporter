"""full_key_id and reply_key_id: the whole message id and the whole quoted id, on both platforms."""
import pytest

from Whatsapp_Chat_Exporter import android_handler, ios_handler
from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message
from Whatsapp_Chat_Exporter.utility import Timing

STANZA_ID = "3EB0C6A2E5D8F1B4A7C9D2E6F8A1B3C5"  # 32 characters, so key_id is a prefix of it
QUOTED_ID = "4FC1D7B3F6E9A2C5B8DAE3F7A9B2C4D6"
OFFSET = Timing(0)


def ios_row(**overrides):
    row = {
        "ZISFROMME": 1,
        "ZMESSAGEDATE": 0,
        "ZSENTDATE": None,
        "ZMESSAGETYPE": 0,
        "ZMETADATA": None,
        "ZSTANZAID": STANZA_ID,
        "ZTEXT": "hi",
        "ZMESSAGETEXT": None,
    }
    row.update(overrides)
    return row


def ios_quoted_metadata(quoted_id):
    return b"\x2a" + bytes([len(quoted_id)]) + quoted_id.encode()


class TestIphoneKeys:
    def test_full_key_id_is_the_whole_stanza_id_and_key_id_its_prefix(self):
        message = ios_handler.new_message(ios_row(), OFFSET)
        assert message.full_key_id == STANZA_ID
        assert message.key_id == STANZA_ID[:17]

    @pytest.mark.parametrize("quoted_id", [QUOTED_ID, "A" * 20, "B" * 22])
    def test_reply_key_id_is_the_whole_quoted_id_and_reply_its_prefix(self, quoted_id):
        message = ios_handler.new_message(ios_row(ZMETADATA=ios_quoted_metadata(quoted_id)), OFFSET)
        ios_handler.process_message_data(
            message, ios_row(ZMETADATA=ios_quoted_metadata(quoted_id)), False, None, {}, False)
        assert message.reply_key_id == quoted_id
        assert message.reply == quoted_id[:17]

    def test_a_message_that_is_not_a_reply_has_no_reply_key_id(self):
        message = ios_handler.new_message(ios_row(), OFFSET)
        ios_handler.process_message_data(message, ios_row(), False, None, {}, False)
        assert message.reply_key_id is None

    def test_no_reply_leaves_reply_key_id_empty(self):
        row = ios_row(ZMETADATA=ios_quoted_metadata(QUOTED_ID))
        message = ios_handler.new_message(row, OFFSET)
        ios_handler.process_message_data(message, row, False, None, {}, True)
        assert message.reply_key_id is None
        assert message.reply is None


def android_row(**overrides):
    row = {
        "_id": 1,
        "key_remote_jid": "85212345678@s.whatsapp.net",
        "chat_subject": None,
        "jid_type": 0,
        "sender_jid_row_id": 0,
        "key_from_me": 1,
        "key_id": STANZA_ID,
        "timestamp": 1463926641000,
        "received_timestamp": None,
        "read_timestamp": None,
        "media_wa_type": 0,
        "data": "hi",
        "status": 0,
        "edit_version": 0,
        "quoted": None,
        "quoted_data": None,
        "media_caption": None,
        "transcription_text": None,
    }
    row.update(overrides)
    return row


def android_message(row):
    data = ChatCollection()
    android_handler._process_single_message(data, row, True, OFFSET)
    return data.get_chat(row["key_remote_jid"]).get_message(row["_id"])


class TestAndroidKeys:
    def test_full_key_id_equals_key_id(self):
        message = android_message(android_row())
        assert message.full_key_id == STANZA_ID
        assert message.key_id == STANZA_ID

    def test_reply_key_id_is_the_quoted_key_id(self):
        message = android_message(android_row(quoted=QUOTED_ID, quoted_data="parent"))
        assert message.reply_key_id == QUOTED_ID
        assert message.reply == QUOTED_ID

    def test_a_message_that_is_not_a_reply_has_no_reply_key_id(self):
        assert android_message(android_row()).reply_key_id is None


class TestJson:
    def test_both_keys_are_written_to_json_and_read_back(self):
        message = Message(from_me=1, timestamp=0, time=0, key_id=STANZA_ID[:17], message_type=0)
        message.full_key_id = STANZA_ID
        message.reply_key_id = QUOTED_ID
        back = Message.from_json(message.to_json())
        assert back.full_key_id == STANZA_ID
        assert back.reply_key_id == QUOTED_ID

    def test_a_message_has_both_keys_even_when_null(self):
        message = Message(from_me=1, timestamp=0, time=0, key_id="k", message_type=0)
        assert message.to_json()["full_key_id"] is None
        assert message.to_json()["reply_key_id"] is None
