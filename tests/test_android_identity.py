import logging
import sqlite3
import types

from Whatsapp_Chat_Exporter.__main__ import process_contacts
from Whatsapp_Chat_Exporter.android_handler import _build_identity_resolver, _set_group_sender, contacts
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
        resolver = _build_identity_resolver(wa_db([(PHONE, "Ana Example", "ana"), ("1@s.whatsapp.net", None, "ben")]))
        assert resolver.contact_names == {PHONE: "Ana Example"}
        assert resolver.push_names == {PHONE: "ana", "1@s.whatsapp.net": "ben"}

    def test_no_table_leaves_the_names_empty_and_the_run_continues(self, caplog):
        data = ChatCollection()
        with caplog.at_level(logging.INFO):
            assert contacts(sqlite3.connect(":memory:"), data, None) is False
        assert data.get_system("identity_resolver").contact_names == {}
        assert data.get_system("identity_resolver").push_names == {}
        assert "sender names are left empty" in caplog.text

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
