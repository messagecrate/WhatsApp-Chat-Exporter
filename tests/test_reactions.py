"""reaction_details: each reaction with its emoji and the reactor's WhatsApp id.

Every database here is built in memory with made-up rows, and every receipt
blob is assembled by hand from the protobuf wire format.
"""
import logging
import sqlite3

from Whatsapp_Chat_Exporter import android_handler, ios_handler
from Whatsapp_Chat_Exporter.data_model import ChatCollection, ChatStore, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver
from Whatsapp_Chat_Exporter.ios_handler import reactions_from_receipt_info
from Whatsapp_Chat_Exporter.utility import Device

GROUP = "120363000000000000@g.us"
PHONE = "85212345678@s.whatsapp.net"
BEN = "85287654321@s.whatsapp.net"
LID = "123456789012345@lid"
UNMAPPED_LID = "543210987654321@lid"
THUMBS_UP = "\U0001F44D"
HEART = "❤️"
TIME_MS = 1712450910123


def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field_bytes(number, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return varint(number << 3 | 2) + varint(len(value)) + value


def field_varint(number, value):
    return varint(number << 3) + varint(value)


def other_reaction(jid, emoji, timestamp=TIME_MS):
    """A 7.1 entry: a reaction by someone else."""
    entry = field_bytes(1, "3A0000000000000000AA") + field_bytes(2, jid)
    if emoji is not None:
        entry += field_bytes(3, emoji)
    return field_bytes(1, entry + field_varint(4, timestamp) + field_varint(5, 0))


def own_reaction(emoji, timestamp=TIME_MS):
    """A 7.2 entry: a reaction by the owner of the phone."""
    entry = field_bytes(1, "3A0000000000000000BB") + field_bytes(2, emoji)
    return field_bytes(2, entry + field_varint(3, timestamp) + field_varint(4, 1))


def receipt_info(*reactions):
    """A ZRECEIPTINFO blob: a receipt (field 2, 3, 4) and, when given, the reactions in field 7."""
    blob = field_bytes(2, field_bytes(1, b"\x01" * 7)) + field_varint(3, 1) + field_varint(4, 2)
    if reactions:
        blob += field_bytes(7, b"".join(reactions))
    return blob


def entry(emoji, jid=None, lid=None, from_me=False):
    return {"emoji": emoji, "from_me": from_me, "jid": jid, "lid": lid, "timestamp": TIME_MS / 1000}


def new_message():
    return Message(from_me=False, timestamp=1463926641, time="10:17", key_id="34B5EF10FBCA37B8E")


def ios_db(blobs):
    """One group chat; message n has ZRECEIPTINFO blobs[n - 1]."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR)")
    db.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEINFO INTEGER)")
    db.execute("CREATE TABLE ZWAMESSAGEINFO (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZRECEIPTINFO BLOB)")
    db.execute("INSERT INTO ZWACHATSESSION VALUES (1, ?)", (GROUP,))
    for pk, blob in enumerate(blobs, start=1):
        db.execute("INSERT INTO ZWAMESSAGE VALUES (?, 1, ?)", (pk, pk))
        db.execute("INSERT INTO ZWAMESSAGEINFO VALUES (?, ?, ?)", (pk, pk, blob))
    return db


def ios_reactions(*blobs, resolver=None):
    """Run the iPhone reaction pass and return the messages it read, in order."""
    data = ChatCollection()
    chat = data.add_chat(GROUP, ChatStore(Device.IOS))
    for pk in range(1, len(blobs) + 1):
        chat.add_message(pk, new_message())
    ios_handler._add_reactions(ios_db(blobs), data, resolver or IdentityResolver())
    return [chat.get_message(pk) for pk in range(1, len(blobs) + 1)]


class TestIosReactions:
    def test_one_reaction(self):
        [message] = ios_reactions(receipt_info(other_reaction(PHONE, THUMBS_UP)))
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE)]

    def test_two_reactions_from_different_reactors(self):
        [message] = ios_reactions(receipt_info(other_reaction(PHONE, THUMBS_UP), other_reaction(BEN, HEART)))
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE), entry(HEART, jid=BEN)]

    def test_a_lid_reactor_the_lid_map_resolves(self):
        [message] = ios_reactions(receipt_info(other_reaction(LID, THUMBS_UP)),
                                  resolver=IdentityResolver(lid_to_phone={LID: PHONE}))
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE, lid=LID)]

    def test_a_lid_reactor_the_lid_map_does_not_resolve(self):
        [message] = ios_reactions(receipt_info(other_reaction(UNMAPPED_LID, THUMBS_UP)),
                                  resolver=IdentityResolver(lid_to_phone={LID: PHONE}))
        assert message.reaction_details == [entry(THUMBS_UP, jid=UNMAPPED_LID, lid=UNMAPPED_LID)]

    def test_the_owners_reaction_has_no_id(self):
        [message] = ios_reactions(receipt_info(own_reaction(HEART)))
        assert message.reaction_details == [entry(HEART, from_me=True)]

    def test_a_blob_with_no_reaction(self):
        [message] = ios_reactions(receipt_info())
        assert message.reaction_details == []

    def test_a_withdrawn_reaction_is_left_out(self):
        [message] = ios_reactions(receipt_info(other_reaction(PHONE, None), other_reaction(BEN, HEART)))
        assert message.reaction_details == [entry(HEART, jid=BEN)]

    def test_a_malformed_blob_gives_no_reaction(self):
        good = receipt_info(other_reaction(PHONE, THUMBS_UP))
        broken, good_after = ios_reactions(good[:-3], good)
        assert broken.reaction_details is None
        assert good_after.reaction_details == [entry(THUMBS_UP, jid=PHONE)]

    def test_reactions_stays_as_upstream_has_it(self):
        [message] = ios_reactions(receipt_info(other_reaction(PHONE, THUMBS_UP)))
        assert message.reactions == {}

    def test_a_message_without_receipt_info_has_an_empty_list(self):
        data = ChatCollection()
        chat = data.add_chat(GROUP, ChatStore(Device.IOS))
        chat.add_message(1, new_message())
        chat.add_message(2, new_message())
        ios_handler._add_reactions(ios_db([receipt_info(own_reaction(HEART))]), data, IdentityResolver())
        assert chat.get_message(2).reaction_details == []

    def test_a_backup_without_receipt_info_leaves_the_field_null(self, caplog):
        data = ChatCollection()
        data.add_chat(GROUP, ChatStore(Device.IOS)).add_message(1, new_message())
        with caplog.at_level(logging.INFO):
            ios_handler._add_reactions(sqlite3.connect(":memory:"), data, IdentityResolver())
        assert data.get_chat(GROUP).get_message(1).reaction_details is None
        assert "reaction_details is left null" in caplog.text

    def test_an_undecodable_blob_is_logged_and_left_null(self, caplog):
        with caplog.at_level(logging.INFO):
            [message] = ios_reactions(b"\xff\xff\xff")
        assert message.reaction_details is None
        assert "1 receipt records could not be decoded" in caplog.text


class TestReceiptInfoDecoding:
    def test_garbage_cannot_be_read(self):
        assert reactions_from_receipt_info(b"\xff\xff\xff") is None
        assert reactions_from_receipt_info(b"\x3f") is None

    def test_no_blob_holds_no_reaction(self):
        assert reactions_from_receipt_info(None) == []

    def test_an_emoji_that_is_not_utf8_cannot_be_read(self):
        assert reactions_from_receipt_info(receipt_info(other_reaction(PHONE, b"\xff"))) is None

    def test_reads_the_time(self):
        assert reactions_from_receipt_info(receipt_info(own_reaction(HEART, timestamp=5))) == [
            (True, None, HEART, 5)]


def android_db(reactions, jid_map=None, chat_jid_row=1):
    """jid rows: 1 group, 2 PHONE, 3 LID, 4 BEN, 5 UNMAPPED_LID. Message row 7 in chat 1,
    whose jid row is `chat_jid_row`.

    reactions: (sender_jid_row_id, from_me, reaction).
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE jid (_id INTEGER PRIMARY KEY, raw_string TEXT)")
    db.executemany("INSERT INTO jid VALUES (?, ?)",
                   [(1, GROUP), (2, PHONE), (3, LID), (4, BEN), (5, UNMAPPED_LID)])
    db.execute("CREATE TABLE chat (_id INTEGER PRIMARY KEY, jid_row_id INTEGER)")
    db.execute("INSERT INTO chat VALUES (1, ?)", (chat_jid_row,))
    db.execute("CREATE TABLE message_add_on (_id INTEGER PRIMARY KEY, parent_message_row_id INTEGER,"
               " chat_row_id INTEGER, from_me INTEGER, sender_jid_row_id INTEGER)")
    db.execute("CREATE TABLE message_add_on_reaction (message_add_on_row_id INTEGER, reaction TEXT,"
               " sender_timestamp INTEGER)")
    for row_id, (sender, from_me, reaction) in enumerate(reactions, start=1):
        db.execute("INSERT INTO message_add_on VALUES (?, 7, 1, ?, ?)", (row_id, from_me, sender))
        db.execute("INSERT INTO message_add_on_reaction VALUES (?, ?, ?)", (row_id, reaction, TIME_MS))
    if jid_map is not None:
        db.execute("CREATE TABLE jid_map (lid_row_id INTEGER, jid_row_id INTEGER)")
        db.executemany("INSERT INTO jid_map VALUES (?, ?)", jid_map)
    return db


def android_reactions(reactions, jid_map=None, chat_jid=GROUP, chat_jid_row=1):
    data = ChatCollection()
    data.add_chat(chat_jid, ChatStore(Device.ANDROID, "Chat")).add_message(7, new_message())
    data.set_system("jid_map_exists", jid_map is not None)
    android_handler._get_reactions(android_db(reactions, jid_map, chat_jid_row), data)
    return data.get_chat(chat_jid).get_message(7)


class TestAndroidReactions:
    def test_one_reaction(self):
        message = android_reactions([(2, 0, THUMBS_UP)])
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE)]

    def test_two_reactions_from_different_reactors(self):
        message = android_reactions([(2, 0, THUMBS_UP), (4, 0, HEART)])
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE), entry(HEART, jid=BEN)]

    def test_a_lid_reactor_jid_map_resolves(self):
        message = android_reactions([(3, 0, THUMBS_UP)], jid_map=[(3, 2)])
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE, lid=LID)]

    def test_a_lid_reactor_jid_map_does_not_resolve(self):
        message = android_reactions([(5, 0, THUMBS_UP)], jid_map=[(3, 2)])
        assert message.reaction_details == [entry(THUMBS_UP, jid=UNMAPPED_LID, lid=UNMAPPED_LID)]

    def test_a_chat_stored_under_the_phone_id_behind_its_lid(self):
        # The message query stores an @lid chat under the phone id jid_map gives.
        message = android_reactions([(3, 0, THUMBS_UP)], jid_map=[(3, 2)], chat_jid=PHONE, chat_jid_row=3)
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE, lid=LID)]
        assert message.reactions == {}  # upstream's map misses this chat, as before

    def test_the_owners_reaction_has_no_id(self):
        message = android_reactions([(None, 1, HEART)])
        assert message.reaction_details == [entry(HEART, from_me=True)]

    def test_a_one_to_one_reaction_without_a_sender_row_is_by_the_other_person(self):
        # Android leaves sender_jid_row_id at 0 in a one-to-one chat, as on message rows.
        message = android_reactions([(0, 0, THUMBS_UP)], chat_jid=PHONE, chat_jid_row=2)
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE)]

    def test_a_one_to_one_reaction_without_a_sender_row_in_a_lid_chat(self):
        message = android_reactions([(0, 0, THUMBS_UP)], jid_map=[(3, 2)], chat_jid=PHONE, chat_jid_row=3)
        assert message.reaction_details == [entry(THUMBS_UP, jid=PHONE, lid=LID)]

    def test_a_one_to_one_reaction_with_a_sender_row_keeps_that_sender(self):
        message = android_reactions([(4, 0, THUMBS_UP)], chat_jid=PHONE, chat_jid_row=2)
        assert message.reaction_details == [entry(THUMBS_UP, jid=BEN)]

    def test_a_group_reaction_without_a_sender_row_has_no_id(self):
        message = android_reactions([(0, 0, THUMBS_UP)])
        assert message.reaction_details == [entry(THUMBS_UP)]

    def test_an_empty_reaction_is_left_out_and_reactions_stays_as_upstream_has_it(self):
        message = android_reactions([(2, 0, ""), (4, 0, HEART)])
        assert message.reaction_details == [entry(HEART, jid=BEN)]
        assert message.reactions == {"85212345678": "", "85287654321": HEART}

    def test_a_message_without_reactions_has_an_empty_list(self):
        assert android_reactions([]).reaction_details == []

    def test_no_reaction_table_leaves_the_field_null(self, caplog):
        data = ChatCollection()
        data.add_chat(GROUP, ChatStore(Device.ANDROID, "Group")).add_message(7, new_message())
        with caplog.at_level(logging.INFO):
            android_handler._get_reactions(sqlite3.connect(":memory:"), data)
        assert data.get_chat(GROUP).get_message(7).reaction_details is None
        assert "message_add_on" in caplog.text


def test_reaction_details_is_written_to_json_and_read_back():
    message = new_message()
    message.reaction_details = [entry(THUMBS_UP, jid=PHONE, lid=LID)]
    assert Message.from_json(message.to_json()).reaction_details == [entry(THUMBS_UP, jid=PHONE, lid=LID)]


def test_a_new_message_has_null_until_reactions_are_read():
    assert new_message().to_json()["reaction_details"] is None


def test_an_older_export_without_the_field_reads_back_null():
    data = new_message().to_json()
    del data["reaction_details"]
    assert Message.from_json(data).reaction_details is None
