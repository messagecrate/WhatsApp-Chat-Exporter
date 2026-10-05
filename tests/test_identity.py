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
