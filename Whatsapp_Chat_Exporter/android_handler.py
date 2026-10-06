#!/usr/bin/python3

import logging
import sqlite3
import os
import shutil
from tqdm import tqdm
from pathlib import Path
from mimetypes import MimeTypes
from markupsafe import escape as htmle
from base64 import b64decode, b64encode
from datetime import datetime
from Whatsapp_Chat_Exporter.data_model import ChatStore, Message
from Whatsapp_Chat_Exporter.identity import (
    NO_FILTER, NO_IDENTITY, IdentityResolver, assign_members, member_entry, reaction_entry,
    start_reaction_details
)
from Whatsapp_Chat_Exporter.utility import MAX_SIZE, ROW_SIZE, JidType, Device, get_jid_map_join
from Whatsapp_Chat_Exporter.utility import rendering, get_file_name, setup_template, get_cond_for_empty
from Whatsapp_Chat_Exporter.utility import get_status_location, convert_time_unit, get_jid_map_selection
from Whatsapp_Chat_Exporter.utility import get_chat_condition, safe_name, bytes_to_readable, determine_metadata




def _build_identity_resolver(db):
    """Read contact names and push names from wa.db into an IdentityResolver.

    Raises sqlite3.Error when wa_contacts cannot be read; the caller decides
    what the run does without names.
    """
    contact_names = {}
    push_names = {}
    rows = db.execute("SELECT jid, display_name, wa_name FROM wa_contacts").fetchall()
    for jid, display_name, wa_name in rows:
        if not jid:
            continue
        if display_name:
            contact_names[jid] = display_name
        if wa_name:
            push_names[jid] = wa_name
    return IdentityResolver(contact_names=contact_names, push_names=push_names)


def contacts(db, data, enrich_from_vcards):
    """
    Process WhatsApp contacts from the database.

    Args:
        db: Database connection
        data: Data store object
        enrich_from_vcards: Path to vCard file for contact enrichment

    Returns:
        bool: False if no contacts found, True otherwise

    Also stores the identity resolver that messages() reads for each group
    sender's names, so this runs before messages().
    """
    c = db.cursor()
    try:
        data.set_system("identity_resolver", _build_identity_resolver(db))
    except sqlite3.Error as e:
        logging.info(f"Contact names could not be read ({e}); sender names are left empty.")
        data.set_system("identity_resolver", IdentityResolver())
        return False
    c.execute("SELECT count() FROM wa_contacts")
    total_row_number = c.fetchone()[0]

    if total_row_number == 0:
        if enrich_from_vcards is not None:
            logging.info(
                "No contacts profiles found in the default database, contacts will be imported from the specified vCard file.")
        else:
            logging.warning(
                "No contacts profiles found in the default database, consider using --enrich-from-vcards for adopting names from exported contacts from Google")
        return False
    else:
        logging.info(f"Processed {total_row_number} contacts")

    c.execute("SELECT jid, COALESCE(display_name, wa_name) as display_name, status FROM wa_contacts;")
    
    with tqdm(total=total_row_number, desc="Processing contacts", unit="contact", leave=False) as pbar:
        while (row := _fetch_row_safely(c)) is not None:
            current_chat = data.add_chat(row["jid"], ChatStore(Device.ANDROID, row["display_name"]))
            if row["status"] is not None:
                current_chat.status = row["status"]
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} contacts in {convert_time_unit(total_time)}")

    return True


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _phone_jid_join(row_id_column, alias, jid_map_exists):
    """Join the phone JID that jid_map gives for the jid row in `row_id_column`, as table `alias`.

    Empty without a jid_map table; the caller then selects NULL in its place.
    """
    if not jid_map_exists:
        return ""
    return f"""LEFT JOIN jid_map {alias}_map
                    ON {alias}_map.lid_row_id = {row_id_column}
                LEFT JOIN jid {alias}
                    ON {alias}._id = {alias}_map.jid_row_id"""


def _group_member_rows(db, jid_map_exists):
    """Rows of (group JID, stored member JID, mapped member JID, is admin); None when there is no member table."""
    if _table_exists(db, "group_participant_user"):
        mapped = "COALESCE(phone_jid.raw_string, user_jid.raw_string)" if jid_map_exists else "user_jid.raw_string"
        jid_map_join = _phone_jid_join("group_participant_user.user_jid_row_id", "phone_jid", jid_map_exists)
        return db.execute(f"""
            SELECT group_jid.raw_string,
                user_jid.raw_string,
                {mapped},
                group_participant_user.rank > 0
            FROM group_participant_user
                INNER JOIN jid group_jid
                    ON group_jid._id = group_participant_user.group_jid_row_id
                INNER JOIN jid user_jid
                    ON user_jid._id = group_participant_user.user_jid_row_id
                {jid_map_join}
            ORDER BY group_participant_user.rowid
        """).fetchall()
    if _table_exists(db, "group_participants"):
        return db.execute("""
            SELECT gjid, jid, jid, admin > 0
            FROM group_participants
            WHERE jid IS NOT NULL AND jid != ''
            ORDER BY rowid
        """).fetchall()
    logging.info("No group member table was found; members is left null.")
    return None


def _add_group_members(db, data, filter_chat=NO_FILTER):
    """Set `members` on every exported group chat: one entry per member the database lists."""
    resolver = data.get_system("identity_resolver") or IdentityResolver()
    entries = {}
    try:
        rows = _group_member_rows(db, data.get_system("jid_map_exists"))
    except sqlite3.Error as e:
        logging.info(f"Group members could not be read ({e}); members is left null.")
        return
    if rows is None:
        return
    for group_jid, stored_jid, mapped_jid, is_admin in rows:
        if data.get_chat(group_jid) is None or not stored_jid:
            continue
        identity = resolver.resolve(stored_jid, mapped_jid=mapped_jid)
        entries.setdefault(group_jid, []).append(member_entry(identity, True, is_admin))
    assign_members(data, entries, filter_chat)


def messages(db, data, media_folder, timezone_offset, filter_date, filter_chat, filter_empty, no_reply):
    """
    Process WhatsApp messages from the database.

    Args:
        db: Database connection
        data: Data store object
        media_folder: Folder containing media files
        timezone_offset: Timezone offset
        filter_date: Date filter condition
        filter_chat: Chat filter conditions
        filter_empty: Filter for empty chats
    """
    c = db.cursor()
    total_row_number = _get_message_count(c, filter_empty, filter_date, filter_chat, data.get_system("jid_map_exists"))

    try:
        content_cursor = _get_messages_cursor_legacy(c, filter_empty, filter_date, filter_chat)
        table_message = False
    except sqlite3.OperationalError as e:
        logging.debug(f'Got sql error "{e}" in _get_message_cursor_legacy trying fallback.\n')
        try:
            content_cursor = _get_messages_cursor_new(
                c,
                filter_empty,
                filter_date,
                filter_chat,
                data.get_system("transcription_selection"),
                data.get_system("jid_map_exists")
            )
            table_message = True
        except Exception as e:
            raise e

    with tqdm(total=total_row_number, desc="Processing messages", unit="msg", leave=False) as pbar:
        while (content := _fetch_row_safely(content_cursor)) is not None:
            _process_single_message(data, content, table_message, timezone_offset)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    _get_reactions(db, data)
    _add_group_members(db, data, filter_chat)
    logging.info(f"Processed {total_row_number} messages in {convert_time_unit(total_time)}")

# Helper functions for message processing

def _get_message_count(cursor, filter_empty, filter_date, filter_chat, jid_map_exists):
    """Get the total number of messages to process."""
    try:
        empty_filter = get_cond_for_empty(filter_empty, "messages.key_remote_jid", "messages.needs_push")
        date_filter = f'AND timestamp {filter_date}' if filter_date is not None else ''
        include_filter = get_chat_condition(
            filter_chat[0], True, ["messages.key_remote_jid", "messages.remote_resource"], "jid", "android")
        exclude_filter = get_chat_condition(
            filter_chat[1], False, ["messages.key_remote_jid", "messages.remote_resource"], "jid", "android")

        cursor.execute(f"""SELECT count()
                      FROM messages
                        INNER JOIN jid
                            ON messages.key_remote_jid = jid.raw_string
                        LEFT JOIN chat
                            ON chat.jid_row_id = jid._id
                      WHERE 1=1
                        {empty_filter}
                        {date_filter}
                        {include_filter}
                        {exclude_filter}""")
    except sqlite3.OperationalError as e:
        logging.debug(f'Got sql error "{e}" in _get_message_count trying fallback.\n')

        empty_filter = get_cond_for_empty(filter_empty, "key_remote_jid", "broadcast")
        date_filter = f'AND timestamp {filter_date}' if filter_date is not None else ''
        remote_jid_selection, group_jid_selection = get_jid_map_selection(jid_map_exists)
        include_filter = get_chat_condition(
            filter_chat[0], True, ["key_remote_jid", "group_sender_jid"], "jid", "android")
        exclude_filter = get_chat_condition(
            filter_chat[1], False, ["key_remote_jid", "group_sender_jid"], "jid", "android")

        cursor.execute(f"""SELECT count(),
                        {remote_jid_selection} as key_remote_jid,
                        {group_jid_selection} as group_sender_jid
                      FROM message
                        LEFT JOIN chat
                            ON chat._id = message.chat_row_id
                        INNER JOIN jid
                            ON jid._id = chat.jid_row_id
                        INNER JOIN jid jid_global
                            ON jid_global._id = chat.jid_row_id
                        LEFT JOIN jid jid_group
                            ON jid_group._id = message.sender_jid_row_id
                        {get_jid_map_join(jid_map_exists)}
                      WHERE 1=1
                        {empty_filter}
                        {date_filter}
                        {include_filter}
                        {exclude_filter}""")
    return cursor.fetchone()[0]


def _get_messages_cursor_legacy(cursor, filter_empty, filter_date, filter_chat):
    """Get cursor for legacy database schema."""
    empty_filter = get_cond_for_empty(filter_empty, "messages.key_remote_jid", "messages.needs_push")
    date_filter = f'AND messages.timestamp {filter_date}' if filter_date is not None else ''
    include_filter = get_chat_condition(
        filter_chat[0], True, ["messages.key_remote_jid", "messages.remote_resource"], "jid_global", "android")
    exclude_filter = get_chat_condition(
        filter_chat[1], False, ["messages.key_remote_jid", "messages.remote_resource"], "jid_global", "android")

    cursor.execute(f"""SELECT messages.key_remote_jid,
                            messages._id,
                            messages.key_from_me,
                            messages.timestamp,
                            messages.data,
                            messages.status,
                            messages.edit_version,
                            messages.thumb_image,
                            messages.remote_resource,
                            CAST(messages.media_wa_type as INTEGER) as media_wa_type,
                            messages.latitude,
                            messages.longitude,
                            messages_quotes.key_id as quoted,
                            messages.key_id,
                            messages_quotes.data as quoted_data,
                            messages.media_caption,
                            missed_call_logs.video_call,
                            chat.subject as chat_subject,
                            message_system.action_type,
                            message_system_group.is_me_joined,
                            jid_old.raw_string as old_jid,
                            jid_new.raw_string as new_jid,
                            jid_global.type as jid_type,
                            COALESCE(receipt_user.receipt_timestamp, messages.received_timestamp) as received_timestamp,
                            COALESCE(receipt_user.read_timestamp, receipt_user.played_timestamp, messages.read_device_timestamp) as read_timestamp
                    FROM messages
                        LEFT JOIN messages_quotes
                            ON messages.quoted_row_id = messages_quotes._id
                        LEFT JOIN missed_call_logs
                            ON messages._id = missed_call_logs.message_row_id
                        INNER JOIN jid jid_global
                            ON messages.key_remote_jid = jid_global.raw_string
                        LEFT JOIN chat
                            ON chat.jid_row_id = jid_global._id
                        LEFT JOIN message_system
                            ON message_system.message_row_id = messages._id
                        LEFT JOIN message_system_group
                            ON message_system_group.message_row_id = messages._id
                        LEFT JOIN message_system_number_change
                            ON message_system_number_change.message_row_id = messages._id
                        LEFT JOIN jid jid_old
                            ON jid_old._id = message_system_number_change.old_jid_row_id
                        LEFT JOIN jid jid_new
                            ON jid_new._id = message_system_number_change.new_jid_row_id
                        LEFT JOIN receipt_user
                            ON receipt_user.message_row_id = messages._id
                    WHERE messages.key_remote_jid <> '-1'
                        {empty_filter}
                        {date_filter}
                        {include_filter}
                        {exclude_filter}
                    GROUP BY messages._id
                    ORDER BY messages.timestamp ASC;""")
    return cursor


def _get_messages_cursor_new(
        cursor,
        filter_empty,
        filter_date,
        filter_chat,
        transcription_selection,
        jid_map_exists
    ):
    """Get cursor for new database schema."""
    empty_filter = get_cond_for_empty(filter_empty, "key_remote_jid", "broadcast")
    date_filter = f'AND message.timestamp {filter_date}' if filter_date is not None else ''
    remote_jid_selection, group_jid_selection = get_jid_map_selection(jid_map_exists)
    include_filter = get_chat_condition(
        filter_chat[0], True, ["key_remote_jid", "group_sender_jid"], "jid_global", "android")
    exclude_filter = get_chat_condition(
        filter_chat[1], False, ["key_remote_jid", "group_sender_jid"], "jid_global", "android")

    cursor.execute(f"""SELECT {remote_jid_selection} as key_remote_jid,
                            message._id,
                            message.from_me as key_from_me,
                            message.timestamp,
                            message.text_data as data,
                            message.status,
                            message_future.version as edit_version,
                            message_thumbnail.thumbnail as thumb_image,
                            message_media.file_path as remote_resource,
                            message_location.latitude,
                            message_location.longitude,
                            message_quoted.key_id as quoted,
                            message.key_id,
                            message_quoted.text_data as quoted_data,
                            message.message_type as media_wa_type,
                            {group_jid_selection} as group_sender_jid,
                            jid_group.raw_string as group_sender_raw_jid,
                            chat.subject as chat_subject,
                            missed_call_logs.video_call,
                            message.sender_jid_row_id,
                            message_system.action_type,
                            message_system_group.is_me_joined,
                            jid_old.raw_string as old_jid,
                            jid_new.raw_string as new_jid,
                            jid_global.type as jid_type,
                            COALESCE(receipt_user.receipt_timestamp, message.received_timestamp) as received_timestamp,
                            COALESCE(receipt_user.read_timestamp, receipt_user.played_timestamp) as read_timestamp,
                            {transcription_selection}
                    FROM message
                        LEFT JOIN message_quoted
                            ON message_quoted.message_row_id = message._id
                        LEFT JOIN message_location
                            ON message_location.message_row_id = message._id
                        LEFT JOIN message_media
                            ON message_media.message_row_id = message._id
                        LEFT JOIN message_thumbnail
                            ON message_thumbnail.message_row_id = message._id
                        LEFT JOIN message_future
                            ON message_future.message_row_id = message._id
                        LEFT JOIN chat
                            ON chat._id = message.chat_row_id
                        INNER JOIN jid jid_global
                            ON jid_global._id = chat.jid_row_id
                        LEFT JOIN jid jid_group
                            ON jid_group._id = message.sender_jid_row_id
                        LEFT JOIN missed_call_logs
                            ON message._id = missed_call_logs.message_row_id
                        LEFT JOIN message_system
                            ON message_system.message_row_id = message._id
                        LEFT JOIN message_system_group
                            ON message_system_group.message_row_id = message._id
                        LEFT JOIN message_system_number_change
                            ON message_system_number_change.message_row_id = message._id
                        LEFT JOIN jid jid_old
                            ON jid_old._id = message_system_number_change.old_jid_row_id
                        LEFT JOIN jid jid_new
                            ON jid_new._id = message_system_number_change.new_jid_row_id
                        LEFT JOIN receipt_user
                            ON receipt_user.message_row_id = message._id
                        {get_jid_map_join(jid_map_exists)}
                    WHERE key_remote_jid <> '-1'
                        {empty_filter}
                        {date_filter}
                        {include_filter}
                        {exclude_filter}
                    GROUP BY message._id;""")
    return cursor


def _fetch_row_safely(cursor):
    """Safely fetch a row from cursor, handling operational errors."""
    while True:
        try:
            content = cursor.fetchone()
            return content
        except sqlite3.OperationalError as e:
            # Not sure how often this might happen, but this check should reduce the overhead
            # if DEBUG flag is not set.
            if logging.isEnabledFor(logging.DEBUG):
                logging.debug(f'Got sql error "{e}" in _fetch_row_safely ignoring row.\n')
            continue


def _process_single_message(data, content, table_message, timezone_offset):
    """Process a single message row."""
    if content["key_remote_jid"] is None:
        return

    # Get or create the chat
    current_chat = data.get_chat(content["key_remote_jid"])
    if current_chat is None:
        current_chat = data.add_chat(content["key_remote_jid"], ChatStore(
            Device.ANDROID, content["chat_subject"]))
    # Determine sender_jid_row_id
    if "sender_jid_row_id" in content:
        sender_jid_row_id = content["sender_jid_row_id"]
    else:
        sender_jid_row_id = None

    # Create message object
    message = Message(
        from_me=not sender_jid_row_id and content["key_from_me"],
        timestamp=content["timestamp"],
        time=content["timestamp"],
        key_id=content["key_id"],
        timezone_offset=timezone_offset,
        message_type=content["media_wa_type"],
        received_timestamp=content["received_timestamp"],
        read_timestamp=content["read_timestamp"],
        full_key_id=content["key_id"]
    )

    # Handle binary data
    if isinstance(content["data"], bytes):
        _process_binary_message(message, content)
        current_chat.add_message(content["_id"], message)
        return

    # Set sender for group chats
    if content["jid_type"] == JidType.GROUP and content["key_from_me"] == 0:
        _set_group_sender(message, content, data, table_message)
    else:
        message.sender = None

    # Handle quoted messages
    if content["quoted"] is not None:
        message.reply = content["quoted"]
        message.reply_key_id = content["quoted"]
        if content["quoted_data"] is not None and len(content["quoted_data"]) > 200:
            message.quoted_data = content["quoted_data"][:201] + "..."
        else:
            message.quoted_data = content["quoted_data"]
    else:
        message.reply = None

    # Handle message caption
    if not table_message and content["media_caption"] is not None:
        # Old schema
        message.caption = content["media_caption"]
    elif table_message:
        # New schema
        if content["media_wa_type"] == 1 and content["data"] is not None:
            message.caption = content["data"]
        elif content["media_wa_type"] == 2 and content["transcription_text"] is not None:
            message.caption = f'"{content["transcription_text"]}"'
    else:
        message.caption = None

    # Handle message content based on status
    if content["status"] == 6:  # 6 = Metadata
        _process_metadata_message(message, content, data, table_message)
    else:
        # Real message
        _process_regular_message(message, content, table_message)

    current_chat.add_message(content["_id"], message)


def _process_binary_message(message, content):
    """Process binary message data."""
    message.data = ("The message is binary data and its base64 is "
                    '<a href="https://gchq.github.io/CyberChef/#recipe=From_Base64'
                    "('A-Za-z0-9%2B/%3D',true,false)Text_Encoding_Brute_Force"
                    f"""('Decode')&input={b64encode(b64encode(content["data"])).decode()}">""")
    message.data += b64encode(content["data"]).decode("utf-8") + "</a>"
    message.safe = message.meta = True


def _row_sender_identity(content, data, table_message):
    """The resolved identity of the member a message row names as its sender, or NO_IDENTITY."""
    stored_jid = mapped_jid = None
    if table_message:
        if content["sender_jid_row_id"] > 0:
            stored_jid, mapped_jid = content["group_sender_raw_jid"], content["group_sender_jid"]
    else:
        stored_jid = content["remote_resource"]
    resolver = data.get_system("identity_resolver") or IdentityResolver()
    return resolver.resolve(stored_jid, mapped_jid=mapped_jid)


def _set_group_sender(message, content, data, table_message):
    """Set sender name and identity for group messages."""
    name = fallback = None
    if table_message:
        if content["sender_jid_row_id"] > 0:
            mapped_jid = content["group_sender_jid"]
            if mapped_jid in data:
                name = data.get_chat(mapped_jid).name
            if "@" in mapped_jid:
                fallback = mapped_jid.split('@')[0]
    else:
        if content["remote_resource"] is not None:
            if content["remote_resource"] in data:
                name = data.get_chat(content["remote_resource"]).name
            if "@" in content["remote_resource"]:
                fallback = content["remote_resource"].split('@')[0]

    message.sender = name or fallback
    identity = _row_sender_identity(content, data, table_message)
    message.sender_jid = identity.jid
    message.sender_lid = identity.lid
    message.sender_contact_name = identity.contact_name
    message.sender_push_name = identity.push_name


# message_system.action_type values that are an action in a group. Both sets
# follow the branches of utility.determine_metadata and change with it;
# tests/test_group_action.py checks the first against it.
# These begin the text with the message's sender: renamed, added, left, icon
# changed, created, added someone, removed someone, description changed.
GROUP_ACTION_TYPES_NAMING_SENDER = frozenset({1, 4, 5, 6, 11, 12, 14, 27})
# These name no member: "You were removed", "Someone joined … invite link".
GROUP_ACTION_TYPES = GROUP_ACTION_TYPES_NAMING_SENDER | {7, 20}


def _process_metadata_message(message, content, data, table_message):
    """Process metadata message."""
    message.meta = True
    name = fallback = None

    if table_message:
        if content["sender_jid_row_id"] > 0:
            _jid = content["group_sender_jid"]
            if _jid in data:
                name = data.get_chat(_jid).name
            if "@" in _jid:
                fallback = _jid.split('@')[0]
        else:
            name = "You"
    else:
        _jid = content["remote_resource"]
        if _jid is not None:
            if _jid in data:
                name = data.get_chat(_jid).name
            if "@" in _jid:
                fallback = _jid.split('@')[0]
        else:
            name = "You"

    message.data = determine_metadata(content, name or fallback)

    if isinstance(message.data, str) and "<br>" in message.data:
        message.safe = True

    if content["jid_type"] == JidType.GROUP and message.data is not None:
        _set_group_action(message, content, name or fallback,
                          _row_sender_identity(content, data, table_message).jid)

    if message.data is None:
        if content["video_call"] is not None:  # Missed call
            message.meta = True
            if content["video_call"] == 1:
                message.data = "A video call was missed"
            elif content["video_call"] == 0:
                message.data = "A voice call was missed"
        elif content["data"] is None and content["thumb_image"] is None:
            message.meta = True
            message.data = None


# Action types whose text quotes the row's data: a group name or description.
GROUP_ACTION_TYPES_QUOTING_DATA = frozenset({1, 11, 27})


def _set_group_action(message, content, name, sender_jid):
    """Set group_action, the text without HTML, and group_action_jid, the member the text names.

    `sender_jid` is the resolved id of the row's sender. An action whose
    text would quote a name or description the row does not hold is left None.
    """
    action_type = content["action_type"]
    me_joined = content["is_me_joined"] == 1
    if not me_joined and action_type not in GROUP_ACTION_TYPES:
        return
    if not me_joined and action_type in GROUP_ACTION_TYPES_QUOTING_DATA and content["data"] is None:
        return
    if not me_joined and action_type == 27:
        # determine_metadata writes the description as HTML; this is the stored text.
        message.group_action = f"{name} changed the group description to:\n{content['data']}"
    else:
        message.group_action = message.data
    if me_joined or action_type in GROUP_ACTION_TYPES_NAMING_SENDER:
        message.group_action_jid = sender_jid


def _process_regular_message(message, content, table_message):
    """Process regular (non-metadata) message."""
    message.sticker = content["media_wa_type"] == 20  # Sticker is a message

    if content["key_from_me"] == 1:
        if content["status"] == 5 and content["edit_version"] == 7 or table_message and content["media_wa_type"] == 15:
            msg = "Message deleted"
            message.meta = True
        else:
            if content["media_wa_type"] == 5:
                msg = f"Location shared: {content['latitude'], content['longitude']}"
                message.meta = True
            else:
                msg = content["data"]
                if msg is not None:
                    msg = _format_message_text(msg)
    else:
        if content["status"] == 0 and content["edit_version"] == 7 or table_message and content["media_wa_type"] == 15:
            msg = "Message deleted"
            message.meta = True
        else:
            if content["media_wa_type"] == 5:
                msg = f"Location shared: {content['latitude'], content['longitude']}"
                message.meta = True
            else:
                msg = content["data"]
                if msg is not None:
                    msg = _format_message_text(msg)

    message.data = msg


def _format_message_text(text):
    """Format message text, replacing newlines with HTML breaks."""
    if "\r\n" in text:
        text = text.replace("\r\n", " <br>")
    if "\n" in text:
        text = text.replace("\n", " <br>")
    return text


def _get_reactions(db, data):
    """
    Process message reactions. Only new schema is supported.
    Chat filter is not applied here at the moment. Maybe in the future.
    """
    c = db.cursor()
    
    try:
        # Check if tables exist, old schema might not have reactions or in somewhere else
        c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='message_add_on'")
        if c.fetchone()[0] == 0:
            logging.info("There is no message_add_on table; reaction_details is left null.")
            return

        logging.info("Processing reactions...", extra={"clear": True})

        # For reaction_details: the phone id behind an @lid reactor, as for a group
        # sender, and behind an @lid chat, which the message query stores under it.
        jid_map_exists = data.get_system("jid_map_exists")
        sender_mapped = "sender_phone_jid.raw_string" if jid_map_exists else "NULL"
        chat_mapped = "chat_phone_jid.raw_string" if jid_map_exists else "NULL"
        sender_phone_join = _phone_jid_join("message_add_on.sender_jid_row_id", "sender_phone_jid", jid_map_exists)
        chat_phone_join = _phone_jid_join("chat.jid_row_id", "chat_phone_jid", jid_map_exists)
        c.execute(f"""
            SELECT
                message_add_on.parent_message_row_id,
                message_add_on_reaction.reaction,
                message_add_on.from_me,
                jid.raw_string as sender_jid_raw,
                {sender_mapped} as sender_jid_mapped,
                chat_jid.raw_string as chat_jid_raw,
                {chat_mapped} as chat_jid_mapped,
                message_add_on_reaction.sender_timestamp
            FROM message_add_on
                INNER JOIN message_add_on_reaction 
                    ON message_add_on._id = message_add_on_reaction.message_add_on_row_id
                LEFT JOIN jid 
                    ON message_add_on.sender_jid_row_id = jid._id
                {sender_phone_join}
                LEFT JOIN chat 
                    ON message_add_on.chat_row_id = chat._id
                LEFT JOIN jid chat_jid 
                    ON chat.jid_row_id = chat_jid._id
                {chat_phone_join}
        """)
    except sqlite3.OperationalError:
        logging.warning(f"Could not fetch reactions (schema might be too old or incompatible)")
        return

    rows = c.fetchall()
    total_row_number = len(rows)
    resolver = data.get_system("identity_resolver") or IdentityResolver()
    start_reaction_details(data)

    with tqdm(total=total_row_number, desc="Processing reactions", unit="reaction", leave=False) as pbar:
        for row in rows:
            parent_id = row["parent_message_row_id"]
            reaction = row["reaction"]
            chat_id = row["chat_jid_raw"]
            _react_timestamp = row["sender_timestamp"]

            if chat_id and chat_id in data:
                chat = data[chat_id]
                if parent_id in chat._messages:
                    message = chat._messages[parent_id]
                    
                    # Determine sender name
                    sender_name = None
                    if row["from_me"]:
                        sender_name = "You"
                    elif row["sender_jid_raw"]:
                        sender_jid = row["sender_jid_raw"]
                        if sender_jid in data:
                            sender_name = data[sender_jid].name
                        if not sender_name:
                            sender_name = sender_jid.split('@')[0] if "@" in sender_jid else sender_jid
                    
                    if not sender_name:
                        sender_name = "Unknown"

                    message.reactions[sender_name] = reaction
            # A second lookup on purpose: the block above keeps upstream's lookup for
            # `reactions`, by the raw chat JID; reaction_details finds the chat as the
            # message query stores it.
            _add_reaction_detail(data, row, resolver)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} reactions in {convert_time_unit(total_time)}")


def _add_reaction_detail(data, row, resolver):
    """Append one reaction row to its message's reaction_details; a withdrawn (empty) one is left out.

    The chat is looked up as the message query stores it: under the phone JID
    behind an @lid chat where jid_map has one.
    """
    if not row["reaction"]:
        return
    chat = data.get_chat(row["chat_jid_mapped"] or row["chat_jid_raw"])
    message = chat.get_message(row["parent_message_row_id"]) if chat is not None else None
    if message is None:
        return
    identity = NO_IDENTITY if row["from_me"] else resolver.resolve(
        row["sender_jid_raw"], mapped_jid=row["sender_jid_mapped"])
    message.reaction_details.append(reaction_entry(row["reaction"], row["from_me"], identity, row["sender_timestamp"]))


def media(db, data, media_folder, filter_date, filter_chat, filter_empty, separate_media=True, fix_dot_files=False):
    """
    Process WhatsApp media files from the database.

    Args:
        db: Database connection
        data: Data store object
        media_folder: Folder containing media files
        filter_date: Date filter condition
        filter_chat: Chat filter conditions
        filter_empty: Filter for empty chats
        separate_media: Whether to separate media files by chat
    """
    c = db.cursor()
    total_row_number = _get_media_count(c, filter_empty, filter_date, filter_chat)
    try:
        content_cursor = _get_media_cursor_legacy(c, filter_empty, filter_date, filter_chat)
    except sqlite3.OperationalError as e:
        logging.debug(f'Got sql error "{e}" in _get_media_cursor_legacy trying fallback.\n')
        content_cursor = _get_media_cursor_new(c, filter_empty, filter_date, filter_chat)

    content = content_cursor.fetchone()
    mime = MimeTypes()

    # Ensure thumbnails directory exists
    Path(f"{media_folder}/thumbnails").mkdir(parents=True, exist_ok=True)

    with tqdm(total=total_row_number, desc="Processing media", unit="media", leave=False) as pbar:
        while (content := _fetch_row_safely(content_cursor)) is not None:
            _process_single_media(data, content, media_folder, mime, separate_media, fix_dot_files)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} media in {convert_time_unit(total_time)}")

# Helper functions for media processing

def _get_media_count(cursor, filter_empty, filter_date, filter_chat):
    """Get the total number of media files to process."""
    try:
        empty_filter = get_cond_for_empty(filter_empty, "key_remote_jid", "messages.needs_push")
        date_filter = f'AND messages.timestamp {filter_date}' if filter_date is not None else ''
        include_filter = get_chat_condition(
            filter_chat[0], True, ["messages.key_remote_jid", "remote_resource"], "jid", "android")
        exclude_filter = get_chat_condition(
            filter_chat[1], False, ["messages.key_remote_jid", "remote_resource"], "jid", "android")

        cursor.execute(f"""SELECT count()
                    FROM message_media
                        INNER JOIN messages
                            ON message_media.message_row_id = messages._id
                        INNER JOIN jid
                            ON messages.key_remote_jid = jid.raw_string
                        LEFT JOIN chat
                            ON chat.jid_row_id = jid._id
                    WHERE 1=1  
                        {empty_filter}
                        {date_filter}
                        {include_filter}
                        {exclude_filter}""")
    except sqlite3.OperationalError as e:
        logging.debug(f'Got sql error "{e}" in _get_media_count trying fallback.\n')
        empty_filter = get_cond_for_empty(filter_empty, "jid.raw_string", "broadcast")
        date_filter = f'AND message.timestamp {filter_date}' if filter_date is not None else ''
        include_filter = get_chat_condition(
            filter_chat[0], True, ["key_remote_jid", "group_sender_jid"], "jid", "android")
        exclude_filter = get_chat_condition(
            filter_chat[1], False, ["key_remote_jid", "group_sender_jid"], "jid", "android")

        cursor.execute(f"""SELECT count(),
                        COALESCE(lid_global.raw_string, jid.raw_string) as key_remote_jid,
                        COALESCE(lid_group.raw_string, jid_group.raw_string) as group_sender_jid
                    FROM message_media
                        INNER JOIN message
                            ON message_media.message_row_id = message._id
                        LEFT JOIN chat
                            ON chat._id = message.chat_row_id
                        INNER JOIN jid
                            ON jid._id = chat.jid_row_id
                        LEFT JOIN jid jid_group
                            ON jid_group._id = message.sender_jid_row_id
                        LEFT JOIN jid_map as jid_map_global
                            ON chat.jid_row_id = jid_map_global.lid_row_id
                        LEFT JOIN jid lid_global
                            ON jid_map_global.jid_row_id = lid_global._id
                        LEFT JOIN jid_map as jid_map_group
                            ON message.sender_jid_row_id = jid_map_group.lid_row_id
                        LEFT JOIN jid lid_group
                            ON jid_map_group.jid_row_id = lid_group._id
                    WHERE 1=1    
                        {empty_filter}
                        {date_filter}
                        {include_filter}
                        {exclude_filter}""")
    return cursor.fetchone()[0]


def _get_media_cursor_legacy(cursor, filter_empty, filter_date, filter_chat):
    """Get cursor for legacy media database schema."""
    empty_filter = get_cond_for_empty(filter_empty, "messages.key_remote_jid", "messages.needs_push")
    date_filter = f'AND messages.timestamp {filter_date}' if filter_date is not None else ''
    include_filter = get_chat_condition(
        filter_chat[0], True, ["messages.key_remote_jid", "remote_resource"], "jid", "android")
    exclude_filter = get_chat_condition(
        filter_chat[1], False, ["messages.key_remote_jid", "remote_resource"], "jid", "android")

    cursor.execute(f"""SELECT messages.key_remote_jid,
                        message_row_id,
                        file_path,
                        message_url,
                        mime_type,
                        media_key,
                        file_hash,
                        thumbnail
                 FROM message_media
                    INNER JOIN messages
                        ON message_media.message_row_id = messages._id
                    LEFT JOIN media_hash_thumbnail
                        ON message_media.file_hash = media_hash_thumbnail.media_hash
                    INNER JOIN jid
                        ON messages.key_remote_jid = jid.raw_string
                    LEFT JOIN chat
                        ON chat.jid_row_id = jid._id
                WHERE jid.type <> 7
                    {empty_filter}
                    {date_filter}
                    {include_filter}
                    {exclude_filter}
                ORDER BY messages.key_remote_jid ASC""")
    return cursor


def _get_media_cursor_new(cursor, filter_empty, filter_date, filter_chat):
    """Get cursor for new media database schema."""
    empty_filter = get_cond_for_empty(filter_empty, "key_remote_jid", "broadcast")
    date_filter = f'AND message.timestamp {filter_date}' if filter_date is not None else ''
    include_filter = get_chat_condition(
        filter_chat[0], True, ["key_remote_jid", "group_sender_jid"], "jid", "android")
    exclude_filter = get_chat_condition(
        filter_chat[1], False, ["key_remote_jid", "group_sender_jid"], "jid", "android")

    cursor.execute(f"""SELECT COALESCE(lid_global.raw_string, jid.raw_string) as key_remote_jid,
                    message_row_id,
                    file_path,
                    message_url,
                    mime_type,
                    media_key,
                    file_hash,
                    thumbnail,
                    COALESCE(lid_group.raw_string, jid_group.raw_string) as group_sender_jid
                FROM message_media
                    INNER JOIN message
                        ON message_media.message_row_id = message._id
                    LEFT JOIN chat
                        ON chat._id = message.chat_row_id
                    INNER JOIN jid
                        ON jid._id = chat.jid_row_id
                    LEFT JOIN media_hash_thumbnail
                        ON message_media.file_hash = media_hash_thumbnail.media_hash
                    LEFT JOIN jid jid_group
                        ON jid_group._id = message.sender_jid_row_id
                    LEFT JOIN jid_map as jid_map_global
                        ON chat.jid_row_id = jid_map_global.lid_row_id
                    LEFT JOIN jid lid_global
                        ON jid_map_global.jid_row_id = lid_global._id
                    LEFT JOIN jid_map as jid_map_group
                        ON message.sender_jid_row_id = jid_map_group.lid_row_id
                    LEFT JOIN jid lid_group
                        ON jid_map_group.jid_row_id = lid_group._id
                WHERE jid.type <> 7
                    {empty_filter}
                    {date_filter}
                    {include_filter}
                    {exclude_filter}
                ORDER BY jid.raw_string ASC""")
    return cursor


def _process_single_media(data, content, media_folder, mime, separate_media, fix_dot_files=False):
    """Process a single media file."""
    file_path = f"{media_folder}/{content['file_path']}"
    current_chat = data.get_chat(content["key_remote_jid"])
    message = current_chat.get_message(content["message_row_id"])
    message.media = True

    if os.path.isfile(file_path):
        # Set mime type
        if content["mime_type"] is None:
            guess = mime.guess_type(file_path)[0]
            if guess is not None:
                message.mime = guess
            else:
                message.mime = "application/octet-stream"
        else:
            message.mime = content["mime_type"]
        
        if fix_dot_files and file_path.endswith("."):
            extension = mime.guess_extension(message.mime)
            if message.mime == "application/octet-stream" or not extension:
                new_file_path = file_path[:-1]
            else:
                extension = mime.guess_extension(message.mime)
                new_file_path = file_path[:-1] + extension
            os.rename(file_path, new_file_path)
            file_path = new_file_path

        # Copy media to separate folder if needed
        if separate_media:
            chat_display_name = safe_name(current_chat.name or message.sender
                                          or content["key_remote_jid"].split('@')[0])
            current_filename = file_path.split("/")[-1]
            new_folder = os.path.join(media_folder, "separated", chat_display_name)
            Path(new_folder).mkdir(parents=True, exist_ok=True)
            new_path = os.path.join(new_folder, current_filename)
            shutil.copy2(file_path, new_path)
            message.data = new_path
        else:
            message.data = file_path
    else:
        message.data = "The media is missing"
        message.mime = "media"
        message.meta = True

    # Handle thumbnail
    if content["thumbnail"] is not None:
        thumb_path = f"{media_folder}/thumbnails/{b64decode(content['file_hash']).hex()}.png"
        if not os.path.isfile(thumb_path):
            with open(thumb_path, "wb") as f:
                f.write(content["thumbnail"])
        message.thumb = thumb_path


def vcard(db, data, media_folder, filter_date, filter_chat, filter_empty):
    """Process vCard data from WhatsApp database and save to files."""
    c = db.cursor()
    try:
        rows = _execute_vcard_query_modern(c, filter_date, filter_chat, filter_empty)
    except sqlite3.OperationalError as e:
        logging.debug(f'Got sql error "{e}" in _execute_vcard_query_modern trying fallback.\n')
        rows = _execute_vcard_query_legacy(c, filter_date, filter_chat, filter_empty)

    total_row_number = len(rows)

    # Create vCards directory if it doesn't exist
    path = os.path.join(media_folder, "vCards")
    Path(path).mkdir(parents=True, exist_ok=True)

    with tqdm(total=total_row_number, desc="Processing vCards", unit="vcard", leave=False) as pbar:
        for row in rows:
            _process_vcard_row(row, path, data)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} vCards in {convert_time_unit(total_time)}")

def _execute_vcard_query_modern(c, filter_date, filter_chat, filter_empty):
    """Execute vCard query for modern WhatsApp database schema."""

    # Build the filter conditions
    date_filter = f'AND messages.timestamp {filter_date}' if filter_date is not None else ''
    empty_filter = get_cond_for_empty(filter_empty, "key_remote_jid", "messages.needs_push")
    include_filter = get_chat_condition(
        filter_chat[0], True, ["key_remote_jid", "group_sender_jid"], "jid", "android")
    exclude_filter = get_chat_condition(
        filter_chat[1], False, ["key_remote_jid", "group_sender_jid"], "jid", "android")

    query = f"""SELECT message_row_id,
                    COALESCE(lid_global.raw_string, jid.raw_string) as key_remote_jid,
                    vcard,
                    messages.media_name,
                    COALESCE(lid_group.raw_string, jid_group.raw_string) as group_sender_jid
                FROM messages_vcards
                    INNER JOIN messages
                        ON messages_vcards.message_row_id = messages._id
                    INNER JOIN jid
                        ON messages.key_remote_jid = jid.raw_string
                    LEFT JOIN chat
                        ON chat.jid_row_id = jid._id
                    LEFT JOIN jid jid_group
                        ON jid_group._id = message.sender_jid_row_id
                    LEFT JOIN jid_map as jid_map_global
                        ON chat.jid_row_id = jid_map_global.lid_row_id
                    LEFT JOIN jid lid_global
                        ON jid_map_global.jid_row_id = lid_global._id
                    LEFT JOIN jid_map as jid_map_group
                        ON message.sender_jid_row_id = jid_map_group.lid_row_id
                    LEFT JOIN jid lid_group
                        ON jid_map_group.jid_row_id = lid_group._id
             WHERE 1=1
                {empty_filter}
                {date_filter}
                {include_filter}
                {exclude_filter}
             ORDER BY messages.key_remote_jid ASC;"""
    c.execute(query)
    return c.fetchall()


def _execute_vcard_query_legacy(c, filter_date, filter_chat, filter_empty):
    """Execute vCard query for legacy WhatsApp database schema."""

    # Build the filter conditions
    chat_filter_include = get_chat_condition(
        filter_chat[0], True, ["key_remote_jid", "jid_group.raw_string"], "jid", "android")
    chat_filter_exclude = get_chat_condition(
        filter_chat[1], False, ["key_remote_jid", "jid_group.raw_string"], "jid", "android")
    date_filter = f'AND message.timestamp {filter_date}' if filter_date is not None else ''
    empty_filter = get_cond_for_empty(filter_empty, "key_remote_jid", "broadcast")

    query = f"""SELECT message_row_id,
                jid.raw_string as key_remote_jid,
                vcard,
                message.text_data as media_name
             FROM message_vcard
                INNER JOIN message
                    ON message_vcard.message_row_id = message._id
                LEFT JOIN chat
                    ON chat._id = message.chat_row_id
                INNER JOIN jid
                    ON jid._id = chat.jid_row_id
                LEFT JOIN jid jid_group
                    ON jid_group._id = message.sender_jid_row_id
            WHERE 1=1
                {empty_filter}
                {date_filter}
                {chat_filter_include}
                {chat_filter_exclude}
             ORDER BY message.chat_row_id ASC;"""
    c.execute(query)
    return c.fetchall()


def _process_vcard_row(row, path, data):
    """Process a single vCard row and save to file."""
    media_name = row["media_name"] if row["media_name"] is not None else "Undefined vCard File"
    file_name = "".join(x for x in media_name if x.isalnum())
    file_name = file_name.encode('utf-8')[:230].decode('utf-8', 'ignore')
    file_path = os.path.join(path, f"{file_name}.vcf")

    if not os.path.isfile(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(row["vcard"])

    message = data.get_chat(row["key_remote_jid"]).get_message(row["message_row_id"])
    message.data = "This media include the following vCard file(s):<br>" \
        f'<a href="{htmle(file_path)}">{htmle(media_name)}</a>'
    message.mime = "text/x-vcard"
    message.meta = True
    message.safe = True


def calls(db, data, timezone_offset, filter_chat):
    """Process call logs from WhatsApp database."""
    c = db.cursor()

    # Check if there are any calls that match the filter
    total_row_number = _get_calls_count(c, filter_chat)
    if total_row_number == 0:
        return

    logging.info(f"Processing calls...({total_row_number})", extra={"clear": True})

    # Fetch call data
    calls_data = _fetch_calls_data(c, filter_chat)

    # Create a chat store for all calls
    chat = ChatStore(Device.ANDROID, "WhatsApp Calls")

    # Process each call
    with tqdm(total=total_row_number, desc="Processing calls", unit="call", leave=False) as pbar:
        while (content := _fetch_row_safely(calls_data)) is not None:
            _process_call_record(content, chat, data, timezone_offset)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']

    # Add the calls chat to the data
    data.add_chat("000000000000000", chat)
    logging.info(f"Processed {total_row_number} calls in {convert_time_unit(total_time)}")

def _get_calls_count(c, filter_chat):
    """Get the count of call records that match the filter."""

    # Build the filter conditions
    include_filter = get_chat_condition(filter_chat[0], True, ["key_remote_jid"])
    exclude_filter = get_chat_condition(filter_chat[1], False, ["key_remote_jid"])

    query = f"""SELECT count(),
                COALESCE(lid_global.raw_string, jid.raw_string) as key_remote_jid
            FROM call_log
                INNER JOIN jid
                    ON call_log.jid_row_id = jid._id
                LEFT JOIN chat
                    ON call_log.jid_row_id = chat.jid_row_id
                LEFT JOIN jid_map as jid_map_global
                    ON chat.jid_row_id = jid_map_global.lid_row_id
                LEFT JOIN jid lid_global
                    ON jid_map_global.jid_row_id = lid_global._id
            WHERE 1=1
                {include_filter}
                {exclude_filter}"""
    c.execute(query)
    return c.fetchone()[0]


def _fetch_calls_data(c, filter_chat):
    """Fetch call data from the database."""

    # Build the filter conditions
    include_filter = get_chat_condition(filter_chat[0], True, ["key_remote_jid"])
    exclude_filter = get_chat_condition(filter_chat[1], False, ["key_remote_jid"])

    query = f"""SELECT call_log._id,
                    COALESCE(lid_global.raw_string, jid.raw_string) as key_remote_jid,
                    from_me,
                    call_id,
                    timestamp,
                    video_call,
                    duration,
                    call_result,
                    bytes_transferred,
                    chat.subject as chat_subject
            FROM call_log
                INNER JOIN jid
                    ON call_log.jid_row_id = jid._id
                LEFT JOIN chat
                    ON call_log.jid_row_id = chat.jid_row_id
                LEFT JOIN jid_map as jid_map_global
                    ON chat.jid_row_id = jid_map_global.lid_row_id
                LEFT JOIN jid lid_global
                    ON jid_map_global.jid_row_id = lid_global._id
            WHERE 1=1
                {include_filter}
                {exclude_filter}"""
    c.execute(query)
    return c


def _process_call_record(content, chat, data, timezone_offset):
    """Process a single call record and add it to the chat."""
    call = Message(
        from_me=content["from_me"],
        timestamp=content["timestamp"],
        time=content["timestamp"],
        key_id=content["call_id"],
        timezone_offset=timezone_offset,
        received_timestamp=None,  # TODO: Add timestamp
        read_timestamp=None,  # TODO: Add timestamp
        full_key_id=content["call_id"]
    )

    # Get caller/callee name
    _jid = content["key_remote_jid"]
    name = data.get_chat(_jid).name if _jid in data else content["chat_subject"] or None
    if _jid is not None and "@" in _jid:
        fallback = _jid.split('@')[0]
    else:
        fallback = None
    call.sender = name or fallback

    # Set metadata
    call.meta = True

    # Construct call description based on call type and result
    call.data = _construct_call_description(content, call)

    # Add call to chat
    chat.add_message(content["_id"], call)


def _construct_call_description(content, call):
    """Construct a description of the call based on its type and result."""
    description = (
        f"A {'video' if content['video_call'] else 'voice'} "
        f"call {'to' if call.from_me else 'from'} "
        f"{call.sender} was "
    )

    if content['call_result'] in (0, 4, 7):
        description += "cancelled." if call.from_me else "missed."
    elif content['call_result'] == 2:
        description += "not answered." if call.from_me else "missed."
    elif content['call_result'] == 3:
        description += "unavailable."
    elif content['call_result'] == 5:
        call_time = convert_time_unit(content['duration'])
        call_bytes = bytes_to_readable(content['bytes_transferred'])
        description += (
            f"initiated and lasted for {call_time} "
            f"with {call_bytes} data transferred."
        )
    else:
        description += "in an unknown state."

    return description


# TODO: Marked for enhancement on multi-threaded processing
def create_html(
    data,
    output_folder,
    template=None,
    embedded=False,
    offline_static=False,
    maximum_size=None,
    no_avatar=False,
    experimental=False,
    headline=None
):
    """Generate HTML chat files from data."""
    template = setup_template(template, no_avatar, experimental)

    total_row_number = len(data)

    # Create output directory if it doesn't exist
    if not os.path.isdir(output_folder):
        os.mkdir(output_folder)

    w3css = get_status_location(output_folder, offline_static)

    with tqdm(total=total_row_number, desc="Generating HTML", unit="file", leave=False) as pbar:
        for contact in data:
            current_chat = data.get_chat(contact)
            if len(current_chat) == 0:
                # Skip empty chats
                continue

            safe_file_name, name = get_file_name(contact, current_chat)

            if maximum_size is not None:
                _generate_paginated_chat(
                    current_chat,
                    safe_file_name,
                    name,
                    contact,
                    output_folder,
                    template,
                    w3css,
                    maximum_size,
                    headline
                )
            else:
                _generate_single_chat(
                    current_chat,
                    safe_file_name,
                    name,
                    contact,
                    output_folder,
                    template,
                    w3css,
                    headline
                )

            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Generated {total_row_number} chats in {convert_time_unit(total_time)}")

def _generate_single_chat(current_chat, safe_file_name, name, contact, output_folder, template, w3css, headline):
    """Generate a single HTML file for a chat."""
    output_file_name = f"{output_folder}/{safe_file_name}.html"
    rendering(
        output_file_name,
        template,
        name,
        current_chat.values(),
        contact,
        w3css,
        current_chat,
        headline,
        False
    )


def _generate_paginated_chat(current_chat, safe_file_name, name, contact, output_folder, template, w3css, maximum_size, headline):
    """Generate multiple HTML files for a chat when pagination is required."""
    current_size = 0
    current_page = 1
    render_box = []

    # Use default maximum size if set to 0
    if maximum_size == 0:
        maximum_size = MAX_SIZE

    last_msg = current_chat.get_last_message().key_id

    for message in current_chat.values():
        # Calculate message size
        if message.data is not None and not message.meta and not message.media:
            current_size += len(message.data) + ROW_SIZE
        else:
            current_size += ROW_SIZE + 100  # Assume media and meta HTML are 100 bytes

        if current_size > maximum_size:
            # Create a new page
            output_file_name = f"{output_folder}/{safe_file_name}-{current_page}.html"
            rendering(
                output_file_name,
                template,
                name,
                render_box,
                contact,
                w3css,
                current_chat,
                headline,
                next=f"{safe_file_name}-{current_page + 1}.html",
                previous=f"{safe_file_name}-{current_page - 1}.html" if current_page > 1 else False
            )
            render_box = [message]
            current_size = 0
            current_page += 1
        else:
            render_box.append(message)
            if message.key_id == last_msg:
                # Last message, create final page
                if current_page == 1:
                    output_file_name = f"{output_folder}/{safe_file_name}.html"
                else:
                    output_file_name = f"{output_folder}/{safe_file_name}-{current_page}.html"
                rendering(
                    output_file_name,
                    template,
                    name,
                    render_box,
                    contact,
                    w3css,
                    current_chat,
                    headline,
                    False,
                    previous=f"{safe_file_name}-{current_page - 1}.html"
                )


def create_txt(data, output):
    """Generate text files from chat data."""
    os.makedirs(output, exist_ok=True)

    for jik, chat in data.items():
        if len(chat) == 0:
            continue

        # Determine file name
        if chat.name is not None:
            contact = chat.name.replace('/', '')
        else:
            contact = jik.replace('+', '')

        output_file = os.path.join(output, f"{contact}.txt")

        with open(output_file, "w", encoding="utf8") as f:
            for message in chat.values():
                # Skip metadata in text format
                if message.meta and message.mime != "media":
                    continue

                # Format the message
                formatted_message = _format_message_for_txt(message, contact)
                f.write(f"{formatted_message}\n")


def _format_message_for_txt(message, contact):
    """Format a message for text output."""
    date = datetime.fromtimestamp(message.timestamp).date()

    # Determine the sender name
    if message.from_me:
        name = "You"
    else:
        name = message.sender if message.sender else contact

    prefix = f"[{date} {message.time}] {name}: "
    prefix_length = len(prefix)

    # Handle different message types
    if message.media and ("/" in message.mime or message.mime == "media"):
        if message.data == "The media is missing":
            message_text = "<The media is missing>"
        else:
            message_text = f"<media file in {message.data}>"
    else:
        if message.data is None:
            message_text = ""
        else:
            message_text = message.data.replace('<br>', f'\n{" " * prefix_length}')

    # Add caption if present
    if message.caption is not None:
        message_text += "\n" + ' ' * len(prefix) + message.caption.replace('<br>', f'\n{" " * prefix_length}')

    return f"{prefix}{message_text}"
