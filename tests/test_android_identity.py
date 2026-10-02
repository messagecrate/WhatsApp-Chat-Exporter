import logging
import sqlite3
import types

from Whatsapp_Chat_Exporter.__main__ import process_contacts
from Whatsapp_Chat_Exporter.android_handler import (
    _add_group_members, _load_identity_names, _set_group_sender, contacts
)
from Whatsapp_Chat_Exporter.data_model import ChatStore
from Whatsapp_Chat_Exporter.utility import Device
from Whatsapp_Chat_Exporter.data_model import ChatCollection, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver

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


class TestMissingContactDatabase:
    def test_a_missing_wa_db_is_logged(self, tmp_path, caplog):
        missing = str(tmp_path / "wa.db")
        args = types.SimpleNamespace(wa=missing, android=True, enrich_from_vcards=None)
        with caplog.at_level(logging.INFO):
            process_contacts(args, ChatCollection())
        assert len([r for r in caplog.records if missing in r.getMessage()]) == 1


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

    def test_no_member_table_leaves_members_null(self):
        data = android_data(False)
        _add_group_members(sqlite3.connect(":memory:"), data)
        assert data.get_chat(GROUP).members is None
        assert data.get_chat(PHONE).members is None

    def test_a_failing_member_query_leaves_members_null(self):
        data = android_data(True)
        db = android_db([(2, 0)])
        _add_group_members(db, data)
        assert data.get_chat(GROUP).members is None

    def test_a_group_without_member_rows_has_an_empty_list(self):
        data = android_data(False)
        _add_group_members(android_db([]), data)
        assert data.get_chat(GROUP).members == []

    def test_a_one_to_one_chat_has_no_member_list(self):
        data = android_data(False)
        _add_group_members(android_db([(2, 0)]), data)
        assert data.get_chat(PHONE).members is None
