"""Who a WhatsApp id belongs to: phone id, @lid id, contact name, push name."""

from typing import Any, Dict, Iterable, List, NamedTuple, Optional

PHONE_SUFFIX = "@s.whatsapp.net"
LID_SUFFIX = "@lid"


class Identity(NamedTuple):
    """What a backup knows about one person. A missing fact is None."""
    jid: Optional[str]
    lid: Optional[str]
    contact_name: Optional[str]
    push_name: Optional[str]


NO_IDENTITY = Identity(None, None, None, None)


def phone_jid(number: Optional[str]) -> Optional[str]:
    """Build a phone JID from a phone number, keeping only its digits."""
    digits = "".join(char for char in (number or "") if char.isdigit())
    return digits + PHONE_SUFFIX if digits else None


def row_value(row: Any, key: str) -> Any:
    """Read a column from a dict or a sqlite3.Row; None when it is not there."""
    return row[key] if key in row.keys() else None


def _first_name(names: Dict[str, str], ids) -> Optional[str]:
    for jid in ids:
        name = names.get(jid)
        if name:
            return name
    return None


class IdentityResolver:
    """Turns the id a backup stores for a person into an Identity."""

    def __init__(
            self,
            lid_to_phone: Optional[Dict[str, str]] = None,
            contact_names: Optional[Dict[str, str]] = None,
            push_names: Optional[Dict[str, str]] = None
    ) -> None:
        self.lid_to_phone = lid_to_phone or {}
        self.contact_names = contact_names or {}
        self.push_names = push_names or {}

    def resolve(
            self,
            stored_jid: Optional[str],
            mapped_jid: Optional[str] = None,
            contact_name: Optional[str] = None
    ) -> Identity:
        """Resolve the id as the backup stores it.

        Args:
            stored_jid: The id in the backup, a phone JID or an @lid JID.
            mapped_jid: The phone JID the database itself maps the id to, if any.
            contact_name: A contact name found beside the id, if any.
        """
        if not stored_jid:
            return NO_IDENTITY
        lid = stored_jid if stored_jid.endswith(LID_SUFFIX) else None
        jid = mapped_jid or self.lid_to_phone.get(stored_jid) or stored_jid
        ids = (stored_jid, jid)
        return Identity(
            jid,
            lid,
            contact_name or _first_name(self.contact_names, ids),
            _first_name(self.push_names, ids),
        )


def member_entry(identity: Identity, active: bool, admin: bool) -> Dict[str, Any]:
    """One entry of a group chat's `members` list."""
    return {
        "jid": identity.jid,
        "lid": identity.lid,
        "contact_name": identity.contact_name,
        "push_name": identity.push_name,
        "active": bool(active),
        "admin": bool(admin),
    }


def merge_members(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge the entries of one group that share a jid, keeping first-seen order.

    A backup can hold two member rows for one person: one under the @lid id and
    one under the phone id. The merged entry keeps every fact either row has,
    and is active or admin when either row is.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        current = merged.get(entry["jid"])
        if current is None:
            merged[entry["jid"]] = dict(entry)
            continue
        for key in ("lid", "contact_name", "push_name"):
            current[key] = current[key] or entry[key]
        for key in ("active", "admin"):
            current[key] = current[key] or entry[key]
    return list(merged.values())
