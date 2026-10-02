from Whatsapp_Chat_Exporter import android_crypt
from Whatsapp_Chat_Exporter.utility import CRYPT14_OFFSETS


def test_crypt14_tries_the_next_known_offset_when_one_does_not_decrypt(monkeypatch):
    second = CRYPT14_OFFSETS[1]
    tried = []

    def attempt(offset_tuple, database, main_key):
        tried.append(offset_tuple)
        if offset_tuple == (second["iv"], second["iv"] + 16, second["db"]):
            return b"decrypted"
        return None

    monkeypatch.setattr(android_crypt, "_attempt_decrypt_task", attempt)

    assert android_crypt._decrypt_crypt14(b"\x00" * 200, b"key") == b"decrypted"
    assert len(tried) == 2
