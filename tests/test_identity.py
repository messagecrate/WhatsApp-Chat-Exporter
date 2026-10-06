
from Whatsapp_Chat_Exporter.data_model import ChatStore
from Whatsapp_Chat_Exporter.identity import (
    Identity, IdentityResolver, group_is_exported, is_one_to_one_chat, member_entry, merge_members,
    phone_jid
)
from Whatsapp_Chat_Exporter.utility import Device

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


def test_a_mapped_id_from_the_caller_wins_over_the_lid_map():
    resolver = IdentityResolver(lid_to_phone={LID: "85287654321@s.whatsapp.net"})
    assert resolver.resolve(LID, mapped_jid=PHONE) == Identity(PHONE, LID, None, None)


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


def test_member_entry_has_the_six_fields():
    entry = member_entry(Identity(PHONE, LID, "Ana Example", "ana"), True, False)
    assert entry == {
        "jid": PHONE, "lid": LID, "contact_name": "Ana Example", "push_name": "ana",
        "active": True, "admin": False,
    }


def test_two_rows_for_one_person_become_one_entry():
    old_row = member_entry(Identity(PHONE, None, "Ana Example", None), False, True)
    new_row = member_entry(Identity(PHONE, LID, None, "ana"), True, False)
    assert merge_members([old_row, new_row]) == [{
        "jid": PHONE, "lid": LID, "contact_name": "Ana Example", "push_name": "ana",
        "active": True, "admin": True,
    }]


def test_different_people_stay_separate_and_in_order():
    ana = member_entry(Identity(PHONE, None, None, None), True, False)
    ben = member_entry(Identity("85287654321@s.whatsapp.net", None, None, None), True, False)
    assert merge_members([ana, ben]) == [ana, ben]


def test_a_chat_has_no_members_until_they_are_set():
    assert ChatStore(Device.IOS).members is None
    assert ChatStore(Device.IOS).to_json()["members"] is None


def test_members_are_written_to_json_and_read_back():
    chat = ChatStore(Device.IOS, "Group")
    chat.members = [member_entry(Identity(PHONE, None, None, None), True, False)]
    assert ChatStore.from_json(chat.to_json()).members == chat.members


def test_a_merge_takes_the_other_chats_members():
    old = ChatStore(Device.IOS, "Group")
    old.members = [member_entry(Identity(PHONE, None, None, None), False, False)]
    new = ChatStore(Device.IOS, "Group")
    new.members = [member_entry(Identity(PHONE, None, None, None), True, False)]
    old.merge_with(new)
    assert old.members == new.members


def test_a_merge_keeps_members_when_the_other_chat_has_none():
    old = ChatStore(Device.IOS, "Group")
    old.members = [member_entry(Identity(PHONE, None, None, None), True, False)]
    old.merge_with(ChatStore(Device.IOS, "Group"))
    assert old.members == [member_entry(Identity(PHONE, None, None, None), True, False)]


class TestGroupIsExported:
    GROUP = "85212345678-1463926641@g.us"

    def test_no_filter_exports_every_group(self):
        assert group_is_exported(self.GROUP, False, (None, None))

    def test_an_excluded_group_is_left_out_even_with_messages(self):
        assert not group_is_exported(self.GROUP, True, (None, ["85212345678"]))

    def test_an_included_group_is_kept_by_its_id_or_by_its_messages(self):
        assert group_is_exported(self.GROUP, False, (["85212345678"], None))
        assert group_is_exported(self.GROUP, True, (["99999"], None))
        assert not group_is_exported(self.GROUP, False, (["99999"], None))


class TestIsOneToOneChat:
    def test_a_phone_or_lid_chat_is_one_to_one(self):
        assert is_one_to_one_chat(PHONE)
        assert is_one_to_one_chat(LID)

    def test_a_group_broadcast_status_newsletter_or_no_chat_is_not(self):
        for jid in ("85212345678-1463926641@g.us", "1234567890@broadcast", "status@broadcast",
                    "120363000000000000@newsletter", None, ""):
            assert not is_one_to_one_chat(jid)


class TestUnknownFlags:
    def test_a_missing_column_leaves_a_flag_null_and_the_merge_keeps_a_known_value(self):
        assert member_entry(Identity(PHONE, None, None, None), None, 1)["active"] is None
        merged = merge_members([
            member_entry(Identity(PHONE, None, None, None), None, None),
            member_entry(Identity(PHONE, LID, None, None), 1, 0),
        ])
        assert merged == [{"jid": PHONE, "lid": LID, "contact_name": None, "push_name": None,
                           "active": True, "admin": False}]
