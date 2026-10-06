"""Media lookup in the iPhone handler, for both shapes of ZMEDIALOCALPATH.

A real iPhone backup stores ZMEDIALOCALPATH both as "Media/..." and as
"/Media/..." (with a leading slash). Both name a file under Message/ in
the app group directory. These tests fail if a change to how the path
is built (for example stripping or rejoining the leading slash so that
"Message/" is lost) stops either shape from being found, or changes the
"data" value the export writes.
"""
from mimetypes import MimeTypes

import pytest

from Whatsapp_Chat_Exporter.data_model import (
    ChatCollection, ChatStore, Message)
from Whatsapp_Chat_Exporter.ios_handler import process_media_item
from Whatsapp_Chat_Exporter.utility import Device

MEDIA_FOLDER = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
CHAT = "1@s.whatsapp.net"
LOCAL_PATH = "Media/1@s.whatsapp.net/a/b/photo.jpg"


def run_media_item(local_path):
    data = ChatCollection()
    chat = ChatStore(Device.IOS)
    data.add_chat(CHAT, chat)
    message = Message(
        from_me=0, timestamp=0, time=0, key_id="k", message_type=1)
    chat.add_message(7, message)
    content = {
        "ZCONTACTJID": CHAT,
        "ZMESSAGE": 7,
        "ZMEDIALOCALPATH": local_path,
        "ZVCARDSTRING": None,
        "ZTITLE": None,
    }
    process_media_item(
        content, data, MEDIA_FOLDER, MimeTypes(), separate_media=False)
    return message


@pytest.fixture
def app_group(tmp_path, monkeypatch):
    """An app group directory holding one file, at Message/LOCAL_PATH."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / MEDIA_FOLDER / "Message" / LOCAL_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"jpeg")


@pytest.mark.parametrize("local_path, data", [
    (LOCAL_PATH, "Message/" + LOCAL_PATH),
    # Upstream writes the leading slash as a double slash in "data". The
    # fork changes existing JSON values only on purpose, so it is pinned.
    ("/" + LOCAL_PATH, "Message//" + LOCAL_PATH),
])
def test_media_found_for_both_path_shapes(app_group, local_path, data):
    message = run_media_item(local_path)

    assert message.meta is False
    assert message.mime == "image/jpeg"
    assert message.data == data


def test_media_absent_from_the_backup_is_marked_missing(app_group):
    message = run_media_item("Media/1@s.whatsapp.net/a/b/other.jpg")

    assert message.meta is True
    assert message.data == "The media is missing"
