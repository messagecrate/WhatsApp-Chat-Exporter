"""Media lookup in the iPhone handler, for the two shapes ZMEDIALOCALPATH takes.

A real iPhone backup stores ZMEDIALOCALPATH both as "Media/..." and as
"/Media/..." (with a leading slash). Both name a file under Message/ in the
app group directory, and both must be found.
"""
import os
from mimetypes import MimeTypes

import pytest

from Whatsapp_Chat_Exporter.data_model import ChatCollection, ChatStore, Message
from Whatsapp_Chat_Exporter.ios_handler import process_media_item
from Whatsapp_Chat_Exporter.utility import Device

MEDIA_FOLDER = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
CHAT = "1@s.whatsapp.net"
RELATIVE = "Media/1@s.whatsapp.net/a/b/photo.jpg"


def run_media_item(local_path):
    data = ChatCollection()
    chat = ChatStore(Device.IOS)
    data.add_chat(CHAT, chat)
    message = Message(from_me=0, timestamp=0, time=0, key_id="k", message_type=1)
    chat.add_message(7, message)
    content = {
        "ZCONTACTJID": CHAT,
        "ZMESSAGE": 7,
        "ZMEDIALOCALPATH": local_path,
        "ZVCARDSTRING": None,
        "ZTITLE": None,
    }
    process_media_item(content, data, MEDIA_FOLDER, MimeTypes(), separate_media=False)
    return message


@pytest.fixture
def media_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / MEDIA_FOLDER / "Message" / RELATIVE
    path.parent.mkdir(parents=True)
    path.write_bytes(b"jpeg")
    return path


@pytest.mark.parametrize("local_path", [RELATIVE, "/" + RELATIVE])
def test_media_found_for_relative_and_leading_slash_paths(media_file, local_path):
    message = run_media_item(local_path)

    assert message.meta is False
    assert message.mime == "image/jpeg"
    assert os.path.samefile(os.path.join(MEDIA_FOLDER, message.data), media_file)


def test_media_absent_from_the_backup_is_marked_missing(media_file):
    message = run_media_item("Media/1@s.whatsapp.net/a/b/other.jpg")

    assert message.meta is True
    assert message.data == "The media is missing"
