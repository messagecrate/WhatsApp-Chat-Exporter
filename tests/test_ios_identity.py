import sqlite3

from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver
from Whatsapp_Chat_Exporter.ios_handler import _load_lid_map, _load_push_names, process_message_data

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
