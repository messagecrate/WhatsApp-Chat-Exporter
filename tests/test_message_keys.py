"""full_key_id and reply_key_id: the whole message id and the whole quoted id, on both platforms."""
import pytest

from Whatsapp_Chat_Exporter import android_handler, exported_handler, ios_handler
from Whatsapp_Chat_Exporter.data_model import ChatCollection, ChatStore, Message
from Whatsapp_Chat_Exporter.utility import Device, Timing

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
        message = ios_handler.message_from_row(ios_row(), OFFSET)
        assert message.full_key_id == STANZA_ID
        assert message.key_id == STANZA_ID[:17]

    def test_a_metadata_message_has_full_key_id(self):
        row = ios_row(ZMESSAGETYPE=6, ZTEXT=None, ZGROUPINFO=None)
        message = ios_handler.message_from_row(row, OFFSET)
        ios_handler.process_message_data(message, row, False, None, {}, False)
        assert message.full_key_id == STANZA_ID

    @pytest.mark.parametrize("quoted_id", [QUOTED_ID, "A" * 20, "B" * 22])
    def test_reply_key_id_is_the_whole_quoted_id_and_reply_its_prefix(self, quoted_id):
        row = ios_row(ZMETADATA=ios_quoted_metadata(quoted_id))
        message = ios_handler.message_from_row(row, OFFSET)
        ios_handler.process_message_data(message, row, False, None, {}, False)
        assert message.reply_key_id == quoted_id
        assert message.reply == quoted_id[:17]

    def test_a_message_that_is_not_a_reply_has_no_reply_key_id(self):
        message = ios_handler.message_from_row(ios_row(), OFFSET)
        ios_handler.process_message_data(message, ios_row(), False, None, {}, False)
        assert message.reply_key_id is None

    def test_the_no_reply_option_leaves_reply_key_id_null(self):
        row = ios_row(ZMETADATA=ios_quoted_metadata(QUOTED_ID))
        message = ios_handler.message_from_row(row, OFFSET)
        ios_handler.process_message_data(message, row, False, None, {}, True)
        assert message.reply_key_id is None
        assert message.reply is None

    def test_reply_key_id_selects_one_message_where_the_prefix_matches_two(self):
        twin = STANZA_ID[:17] + "0000000000000000"  # same first 17 characters, another id
        rows = [ios_row(ZSTANZAID=STANZA_ID), ios_row(ZSTANZAID=twin),
                ios_row(ZSTANZAID="5AD2E8C4F7A1B3D6", ZMETADATA=ios_quoted_metadata(twin))]
        messages = [ios_handler.message_from_row(row, OFFSET) for row in rows]
        for message, row in zip(messages, rows):
            ios_handler.process_message_data(message, row, False, None, {}, False)
        first, second, reply = messages
        assert first.key_id == second.key_id == reply.reply
        assert second.full_key_id == reply.reply_key_id != first.full_key_id

    def test_a_call_has_full_key_id(self):
        chat = ChatStore(Device.IOS)
        content = {"ZDATE": 0, "ZINCOMING": 1, "ZCALLIDSTRING": "call-1",
                   "ZGROUPCALLCREATORUSERJIDSTRING": None, "ZGROUPJIDSTRING": None,
                   "ZOUTCOME": 0, "ZDURATION": 0, "ZVIDEO": 0, "bytes_transferred": 0}
        ios_handler.process_call_record(content, chat, ChatCollection(), OFFSET)
        assert chat.get_message("call-1").full_key_id == "call-1"


def android_row(**overrides):
    row = {
        "_id": 1,
        "key_remote_jid": "85212345678@s.whatsapp.net",
        "chat_subject": None,
        "jid_type": 0,
        "sender_jid_row_id": 0,
        "remote_resource": None,
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


def android_message(row, table_message=True):
    data = ChatCollection()
    android_handler._process_single_message(data, row, table_message, OFFSET)
    return data.get_chat(row["key_remote_jid"]).get_message(row["_id"])


class TestAndroidKeys:
    def test_full_key_id_equals_key_id(self):
        message = android_message(android_row())
        assert message.full_key_id == STANZA_ID
        assert message.key_id == STANZA_ID

    def test_a_legacy_schema_row_has_full_key_id(self):
        assert android_message(android_row(), table_message=False).full_key_id == STANZA_ID

    def test_a_binary_row_has_full_key_id(self):
        assert android_message(android_row(data=b"\x00\x01")).full_key_id == STANZA_ID

    def test_reply_key_id_is_the_quoted_key_id(self):
        message = android_message(android_row(quoted=QUOTED_ID, quoted_data="parent"))
        assert message.reply_key_id == QUOTED_ID
        assert message.reply == QUOTED_ID

    def test_a_message_that_is_not_a_reply_has_no_reply_key_id(self):
        assert android_message(android_row()).reply_key_id is None

    def test_a_call_has_full_key_id(self):
        chat = ChatStore(Device.ANDROID)
        content = {"_id": 9, "call_id": "call-1", "key_remote_jid": "85212345678@s.whatsapp.net",
                   "chat_subject": None, "from_me": 0, "timestamp": 1463926641000,
                   "video_call": 0, "duration": 0, "call_result": 0}
        android_handler._process_call_record(content, chat, ChatCollection(), OFFSET)
        assert chat.get_message(9).full_key_id == "call-1"


class TestJson:
    def test_both_keys_are_written_to_json_and_read_back(self):
        message = Message(from_me=1, timestamp=0, time=0, key_id=STANZA_ID[:17], full_key_id=STANZA_ID)
        message.reply_key_id = QUOTED_ID
        back = Message.from_json(message.to_json())
        assert back.full_key_id == STANZA_ID
        assert back.reply_key_id == QUOTED_ID

    def test_both_keys_are_null_unless_given(self):
        message = Message(from_me=1, timestamp=0, time=0, key_id="k")
        assert message.to_json()["full_key_id"] is None
        assert message.to_json()["reply_key_id"] is None

    def test_a_message_from_a_text_export_has_no_full_key_id(self):
        chat = ChatStore(Device.EXPORTED)
        exported_handler.process_new_message("01/01/2024, 10:00", "Ana: hi", 1, chat, "Ana", "chat.txt", True, True)
        assert chat.get_message(1).key_id == 1
        assert chat.get_message(1).full_key_id is None

    def test_an_older_json_without_full_key_id_reads_back_null(self):
        message = Message(from_me=1, timestamp=0, time=0, key_id=STANZA_ID[:17], full_key_id=STANZA_ID)
        data = message.to_json()
        del data["full_key_id"]
        assert Message.from_json(data).full_key_id is None
