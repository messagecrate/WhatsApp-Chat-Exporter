#!/usr/bin/python3

import os
import pathlib
import logging
import shutil
import sqlite3
from contextlib import closing
from glob import glob
from tqdm import tqdm
from pathlib import Path
from mimetypes import MimeTypes
from markupsafe import escape as htmle
from Whatsapp_Chat_Exporter.data_model import ChatStore, Message
from Whatsapp_Chat_Exporter.identity import IdentityResolver, member_entry, merge_members, phone_jid
from Whatsapp_Chat_Exporter.utility import APPLE_TIME, get_chat_condition, Device
from Whatsapp_Chat_Exporter.utility import bytes_to_readable, convert_time_unit, safe_name




def contacts(db, data):
    """Process WhatsApp contacts with status information."""
    c = db.cursor()
    c.execute("""SELECT count() FROM ZWAADDRESSBOOKCONTACT WHERE ZABOUTTEXT IS NOT NULL""")
    total_row_number = c.fetchone()[0]
    logging.info(f"Pre-processing contacts...({total_row_number})", extra={"clear": True})

    c.execute("""SELECT ZWHATSAPPID, ZABOUTTEXT FROM ZWAADDRESSBOOKCONTACT WHERE ZABOUTTEXT IS NOT NULL""")
    with tqdm(total=total_row_number, desc="Processing contacts", unit="contact", leave=False) as pbar:
        while (content := c.fetchone()) is not None:
            zwhatsapp_id = content["ZWHATSAPPID"]
            if not zwhatsapp_id.endswith("@s.whatsapp.net"):
                zwhatsapp_id += "@s.whatsapp.net"

            current_chat = ChatStore(Device.IOS)
            current_chat.status = content["ZABOUTTEXT"]
            data.add_chat(zwhatsapp_id, current_chat)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Pre-processed {total_row_number} contacts in {convert_time_unit(total_time)}")


def process_contact_avatars(current_chat, media_folder, contact_id):
    """Process and assign avatar images for a contact."""
    path = f'{media_folder}/Media/Profile/{contact_id.split("@")[0]}'
    avatars = glob(f"{path}*")

    if 0 < len(avatars) <= 1:
        current_chat.their_avatar = avatars[0]
    else:
        for avatar in avatars:
            if avatar.endswith(".thumb") and current_chat.their_avatar_thumb is None:
                current_chat.their_avatar_thumb = avatar
            elif avatar.endswith(".jpg") and current_chat.their_avatar is None:
                current_chat.their_avatar = avatar


def get_contact_name(content):
    """Determine the appropriate contact name based on push name and partner name."""
    is_phone = content["ZPARTNERNAME"].replace("+", "").replace(" ", "").isdigit()
    if content["ZPUSHNAME"] is None or (content["ZPUSHNAME"] and not is_phone):
        return content["ZPARTNERNAME"]
    else:
        return content["ZPUSHNAME"]


def _load_lid_map(media_folder):
    """Map each @lid JID in LID.sqlite to a phone JID. Empty when there is no such file."""
    path = os.path.join(media_folder, "LID.sqlite") if media_folder else None
    if path is None or not os.path.isfile(path):
        logging.info("LID.sqlite was not found; a sender stored under an @lid id keeps that id.")
        return {}
    try:
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as lid_db:
            rows = lid_db.execute(
                "SELECT ZIDENTIFIER, ZPHONENUMBER FROM ZWAZACCOUNT WHERE ZPHONENUMBER IS NOT NULL"
            ).fetchall()
    except sqlite3.Error as e:
        logging.info(f"LID.sqlite could not be read ({e}); a sender stored under an @lid id keeps that id.")
        return {}
    return {lid: jid for lid, number in rows if (jid := phone_jid(number)) is not None}


def _load_push_names(db):
    """Map each JID in ZWAPROFILEPUSHNAME to its push name. Empty when there is no such table."""
    try:
        rows = db.execute(
            "SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZPUSHNAME IS NOT NULL"
        ).fetchall()
    except sqlite3.Error as e:
        logging.info(f"Push names could not be read ({e}); sender_push_name is left empty.")
        return {}
    return {row[0]: row[1] for row in rows if row[0]}


def _load_member_contact_names(db):
    """Map each member JID in ZWAGROUPMEMBER to its contact name, else its first name.

    Empty when there is no such table. The first named row for a JID wins.
    """
    try:
        rows = db.execute(
            "SELECT ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME FROM ZWAGROUPMEMBER ORDER BY Z_PK"
        ).fetchall()
    except sqlite3.Error as e:
        logging.info(f"Member contact names could not be read ({e}); sender_contact_name is left empty.")
        return {}
    names = {}
    for member_jid, contact_name, first_name in rows:
        name = contact_name or first_name
        if member_jid and name and member_jid not in names:
            names[member_jid] = name
    return names


def _build_identity_resolver(db, media_folder):
    """Load what the backup knows about people, once per run."""
    return IdentityResolver(
        lid_to_phone=_load_lid_map(media_folder),
        contact_names=_load_member_contact_names(db),
        push_names=_load_push_names(db),
    )


def _add_group_members(db, data, identity_resolver):
    """Set `members` on every group chat: one entry per person with a member row."""
    entries = {}
    try:
        rows = db.execute("""
            SELECT ZWACHATSESSION.ZCONTACTJID,
                ZWAGROUPMEMBER.ZMEMBERJID,
                ZWAGROUPMEMBER.ZCONTACTNAME,
                ZWAGROUPMEMBER.ZFIRSTNAME,
                ZWAGROUPMEMBER.ZISACTIVE,
                ZWAGROUPMEMBER.ZISADMIN
            FROM ZWAGROUPMEMBER
                INNER JOIN ZWACHATSESSION
                    ON ZWAGROUPMEMBER.ZCHATSESSION = ZWACHATSESSION.Z_PK
            WHERE ZWAGROUPMEMBER.ZMEMBERJID IS NOT NULL
            ORDER BY ZWAGROUPMEMBER.Z_PK
        """).fetchall()
    except sqlite3.Error as e:
        logging.info(f"Group members could not be read ({e}); members is left null.")
        return
    for chat_jid, member_jid, contact_name, first_name, is_active, is_admin in rows:
        if data.get_chat(chat_jid) is None or not member_jid:
            continue
        identity = identity_resolver.resolve(member_jid, contact_name=contact_name or first_name)
        entries.setdefault(chat_jid, []).append(member_entry(identity, is_active, is_admin))
    for chat_jid, chat in data.items():
        if chat_jid.endswith("@g.us"):
            chat.members = merge_members(entries.get(chat_jid, []))


def messages(db, data, media_folder, timezone_offset, filter_date, filter_chat, filter_empty, no_reply):
    """Process WhatsApp messages and contacts from the database."""
    c = db.cursor()
    cursor2 = db.cursor()
    identity_resolver = _build_identity_resolver(db, media_folder)

    # Build the chat filter conditions
    chat_filter_include = get_chat_condition(
        filter_chat[0], True, ["ZWACHATSESSION.ZCONTACTJID", "ZMEMBERJID"], "ZGROUPINFO", "ios")
    chat_filter_exclude = get_chat_condition(
        filter_chat[1], False, ["ZWACHATSESSION.ZCONTACTJID", "ZMEMBERJID"], "ZGROUPINFO", "ios")
    date_filter = f'AND ZMESSAGEDATE {filter_date}' if filter_date is not None else ''

    # Process contacts first
    contact_query = f"""
        SELECT count() 
        FROM (SELECT DISTINCT ZCONTACTJID,
            ZPARTNERNAME,
            ZWAPROFILEPUSHNAME.ZPUSHNAME
        FROM ZWACHATSESSION
            INNER JOIN ZWAMESSAGE
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
            LEFT JOIN ZWAPROFILEPUSHNAME
                ON ZWACHATSESSION.ZCONTACTJID = ZWAPROFILEPUSHNAME.ZJID
            LEFT JOIN ZWAGROUPMEMBER
                    ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        WHERE 1=1
            {chat_filter_include}
            {chat_filter_exclude}
        GROUP BY ZCONTACTJID);
    """
    c.execute(contact_query)
    total_row_number = c.fetchone()[0]

    # Get distinct contacts
    contacts_query = f"""
        SELECT DISTINCT ZCONTACTJID,
            ZPARTNERNAME,
            ZWAPROFILEPUSHNAME.ZPUSHNAME
        FROM ZWACHATSESSION
            INNER JOIN ZWAMESSAGE
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
            LEFT JOIN ZWAPROFILEPUSHNAME
                ON ZWACHATSESSION.ZCONTACTJID = ZWAPROFILEPUSHNAME.ZJID
            LEFT JOIN ZWAGROUPMEMBER
                    ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        WHERE 1=1
            {chat_filter_include}
            {chat_filter_exclude}
        GROUP BY ZCONTACTJID;
    """
    c.execute(contacts_query)

    # Process each contact
    with tqdm(total=total_row_number, desc="Processing contacts", unit="contact", leave=False) as pbar:
        while (content := c.fetchone()) is not None:
            contact_name = get_contact_name(content)
            contact_id = content["ZCONTACTJID"]

            # Add or update chat
            if contact_id not in data:
                current_chat = data.add_chat(contact_id, ChatStore(Device.IOS, contact_name, media_folder))
            else:
                current_chat = data.get_chat(contact_id)
                current_chat.name = contact_name
                current_chat.my_avatar = os.path.join(media_folder, "Media/Profile/Photo.jpg")

            # Process avatar images
            process_contact_avatars(current_chat, media_folder, contact_id)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} contacts in {convert_time_unit(total_time)}")

    # Get message count
    message_count_query = f"""
        SELECT count()
        FROM ZWAMESSAGE
            INNER JOIN ZWACHATSESSION
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
            LEFT JOIN ZWAGROUPMEMBER
                ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        WHERE 1=1
            {date_filter}
            {chat_filter_include}
            {chat_filter_exclude}
    """
    c.execute(message_count_query)
    total_row_number = c.fetchone()[0]
    logging.info(f"Processing messages...(0/{total_row_number})", extra={"clear": True})

    # Fetch messages
    messages_query = f"""
        SELECT ZCONTACTJID,
            ZWAMESSAGE.Z_PK,
            ZISFROMME,
            ZMESSAGEDATE,
            ZTEXT,
            ZMESSAGETYPE,
            ZWAGROUPMEMBER.ZMEMBERJID,
            ZMETADATA,
            ZSTANZAID,
            ZGROUPINFO,
            ZSENTDATE
        FROM ZWAMESSAGE
            LEFT JOIN ZWAGROUPMEMBER
                ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
            LEFT JOIN ZWAMEDIAITEM
                ON ZWAMESSAGE.Z_PK = ZWAMEDIAITEM.ZMESSAGE
            INNER JOIN ZWACHATSESSION
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
        WHERE 1=1   
            {date_filter}
            {chat_filter_include}
            {chat_filter_exclude}
        ORDER BY ZMESSAGEDATE ASC;
    """
    c.execute(messages_query)

    reply_query = """SELECT ZSTANZAID,
                        ZTEXT,
                        ZTITLE
                     FROM ZWAMESSAGE
                        LEFT JOIN ZWAMEDIAITEM
                            ON ZWAMESSAGE.Z_PK = ZWAMEDIAITEM.ZMESSAGE
                     WHERE ZTEXT IS NOT NULL
                        OR ZTITLE IS NOT NULL;"""
    cursor2.execute(reply_query)
    message_map = {row[0][:17]: row[1] or row[2] for row in cursor2.fetchall() if row[0]}

    # Process each message
    with tqdm(total=total_row_number, desc="Processing messages", unit="msg", leave=False) as pbar:
        while (content := c.fetchone()) is not None:
            contact_id = content["ZCONTACTJID"]
            message_pk = content["Z_PK"]
            is_group_message = content["ZGROUPINFO"] is not None

            # Ensure chat exists
            if contact_id not in data:
                current_chat = data.add_chat(contact_id, ChatStore(Device.IOS))
                process_contact_avatars(current_chat, media_folder, contact_id)
            else:
                current_chat = data.get_chat(contact_id)

            # Create message object
            ts = APPLE_TIME + content["ZMESSAGEDATE"]
            message = Message(
                from_me=content["ZISFROMME"],
                timestamp=ts,
                time=ts,
                key_id=content["ZSTANZAID"][:17],
                timezone_offset=timezone_offset,
                message_type=content["ZMESSAGETYPE"],
                received_timestamp=APPLE_TIME + content["ZSENTDATE"] if content["ZSENTDATE"] else None,
                read_timestamp=None  # TODO: Add timestamp
            )

            # Process message data
            invalid = process_message_data(
                message, content, is_group_message, data, message_map, no_reply, identity_resolver)

            # Add valid messages to chat
            if not invalid:
                current_chat.add_message(message_pk, message)

            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} messages in {convert_time_unit(total_time)}")
    _add_group_members(db, data, identity_resolver)


def process_message_data(message, content, is_group_message, data, message_map, no_reply,
                         identity_resolver=None):
    """Process and set message data from content row."""
    # Handle group sender info
    if is_group_message and content["ZISFROMME"] == 0:
        name = None
        if content["ZMEMBERJID"] is not None:
            if content["ZMEMBERJID"] in data:
                name = data.get_chat(content["ZMEMBERJID"]).name
            if "@" in content["ZMEMBERJID"]:
                fallback = content["ZMEMBERJID"].split('@')[0]
            else:
                fallback = None
        else:
            fallback = None
        message.sender = name or fallback
        identity = (identity_resolver or IdentityResolver()).resolve(content["ZMEMBERJID"])
        message.sender_jid = identity.jid
        message.sender_lid = identity.lid
        message.sender_contact_name = identity.contact_name
        message.sender_push_name = identity.push_name
    else:
        message.sender = None

    # Handle metadata messages
    if content["ZMESSAGETYPE"] == 6:
        return process_metadata_message(message, content, is_group_message)

    # Handle quoted replies
    metadata = content["ZMETADATA"]
    if metadata is not None and not no_reply and len(metadata) >= 2 and metadata[0] == 0x2A:
        # The byte after the 0x2A tag is the length of the quoted message ID,
        # which is not always 0x14, so read it instead of assuming it.
        quoted_msg_id_length = metadata[1]
        quoted = metadata[2:2 + quoted_msg_id_length]
        if len(quoted) == quoted_msg_id_length and quoted.isascii():
            message.reply = quoted.decode("ascii")[:17]
            message.quoted_data = message_map.get(message.reply)

    # Handle stickers
    if content["ZMESSAGETYPE"] == 15:
        message.sticker = True

    # Process message text
    process_message_text(message, content)

    return False  # Message is valid


def process_metadata_message(message, content, is_group_message):
    """Process metadata messages (action_type 6)."""
    if is_group_message:
        # Group
        if content["ZTEXT"] is not None:
            # Changed name
            try:
                int(content["ZTEXT"])
            except ValueError:
                msg = f"The group name changed to {content['ZTEXT']}"
                message.data = msg
                message.meta = True
                return False  # Valid message
            else:
                return True  # Invalid message
        else:
            message.data = None
            return False
    else:
        message.data = None
        return False


def process_message_text(message, content):
    """Process and format message text content."""
    if content["ZISFROMME"] == 1:
        if content["ZMESSAGETYPE"] == 14:
            msg = "Message deleted"
            message.meta = True
        else:
            msg = content["ZTEXT"]
            if msg is not None:
                msg = msg.replace("\r\n", "<br>").replace("\n", "<br>")
    else:
        if content["ZMESSAGETYPE"] == 14:
            msg = "Message deleted"
            message.meta = True
        else:
            msg = content["ZTEXT"]
            if msg is not None:
                msg = msg.replace("\r\n", "<br>").replace("\n", "<br>")

    message.data = msg


def media(db, data, media_folder, filter_date, filter_chat, filter_empty, separate_media=False, fix_dot_files=False):
    """Process media files from WhatsApp messages."""
    c = db.cursor()

    # Build filter conditions
    chat_filter_include = get_chat_condition(
        filter_chat[0], True, ["ZWACHATSESSION.ZCONTACTJID", "ZMEMBERJID"], "ZGROUPINFO", "ios")
    chat_filter_exclude = get_chat_condition(
        filter_chat[1], False, ["ZWACHATSESSION.ZCONTACTJID", "ZMEMBERJID"], "ZGROUPINFO", "ios")
    date_filter = f'AND ZMESSAGEDATE {filter_date}' if filter_date is not None else ''

    # Get media count
    media_count_query = f"""
        SELECT count()
        FROM ZWAMEDIAITEM
            INNER JOIN ZWAMESSAGE
                ON ZWAMEDIAITEM.ZMESSAGE = ZWAMESSAGE.Z_PK
            INNER JOIN ZWACHATSESSION
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
            LEFT JOIN ZWAGROUPMEMBER
                ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        WHERE 1=1
            {date_filter}
            {chat_filter_include}
            {chat_filter_exclude}
    """
    c.execute(media_count_query)
    total_row_number = c.fetchone()[0]
    logging.info(f"Processing media...(0/{total_row_number})", extra={"clear": True})

    # Fetch media items
    media_query = f"""
        SELECT ZCONTACTJID,
            ZMESSAGE,
            ZMEDIALOCALPATH,
            ZMEDIAURL,
            ZVCARDSTRING,
            ZMEDIAKEY,
            ZTITLE
        FROM ZWAMEDIAITEM
            INNER JOIN ZWAMESSAGE
                ON ZWAMEDIAITEM.ZMESSAGE = ZWAMESSAGE.Z_PK
            INNER JOIN ZWACHATSESSION
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
            LEFT JOIN ZWAGROUPMEMBER
                ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        WHERE ZMEDIALOCALPATH IS NOT NULL
            {date_filter}
            {chat_filter_include}
            {chat_filter_exclude}
        ORDER BY ZCONTACTJID ASC
    """
    c.execute(media_query)

    # Process each media item
    mime = MimeTypes()
    with tqdm(total=total_row_number, desc="Processing media", unit="media", leave=False) as pbar:
        while (content := c.fetchone()) is not None:
            process_media_item(content, data, media_folder, mime, separate_media, fix_dot_files)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} media in {convert_time_unit(total_time)}")


def process_media_item(content, data, media_folder, mime, separate_media, fix_dot_files=False):
    """Process a single media item."""
    file_path = f"{media_folder}/Message/{content['ZMEDIALOCALPATH']}"
    current_chat = data.get_chat(content["ZCONTACTJID"])
    message = current_chat.get_message(content["ZMESSAGE"])
    message.media = True

    if current_chat.media_base == "":
        current_chat.media_base = media_folder + "/"

    if os.path.isfile(file_path):
        # Set MIME type
        if content["ZVCARDSTRING"] is None:
            guess = mime.guess_type(file_path)[0]
            message.mime = guess if guess is not None else "application/octet-stream"
        else:
            message.mime = content["ZVCARDSTRING"]
        
        if fix_dot_files and file_path.endswith("."):
            extension = mime.guess_extension(message.mime)
            if message.mime == "application/octet-stream" or not extension:
                new_file_path = file_path[:-1]
            else:
                extension = mime.guess_extension(message.mime)
                new_file_path = file_path[:-1] + extension
            os.rename(file_path, new_file_path)
            file_path = new_file_path

        # Handle separate media option
        if separate_media:
            chat_display_name = safe_name(
                current_chat.name or message.sender or content["ZCONTACTJID"].split('@')[0])
            current_filename = file_path.split("/")[-1]
            new_folder = os.path.join(media_folder, "separated", chat_display_name)
            Path(new_folder).mkdir(parents=True, exist_ok=True)
            new_path = os.path.join(new_folder, current_filename)
            shutil.copy2(file_path, new_path)
            message.data = '/'.join(new_path.split("/")[1:])
        else:
            message.data = '/'.join(file_path.split("/")[1:])
    else:
        # Handle missing media
        message.data = "The media is missing"
        message.mime = "media"
        message.meta = True

    # Add caption if available
    if content["ZTITLE"] is not None:
        message.caption = content["ZTITLE"]


def vcard(db, data, media_folder, filter_date, filter_chat, filter_empty):
    """Process vCard contacts from WhatsApp messages."""
    c = db.cursor()

    # Build filter conditions
    chat_filter_include = get_chat_condition(
        filter_chat[0], True, ["ZCONTACTJID", "ZMEMBERJID"], "ZGROUPINFO", "ios")
    chat_filter_exclude = get_chat_condition(
        filter_chat[1], False, ["ZCONTACTJID", "ZMEMBERJID"], "ZGROUPINFO", "ios")
    date_filter = f'AND ZWAMESSAGE.ZMESSAGEDATE {filter_date}' if filter_date is not None else ''

    # Fetch vCard mentions
    vcard_query = f"""
        SELECT DISTINCT ZWAVCARDMENTION.ZMEDIAITEM,
            ZWAMEDIAITEM.ZMESSAGE,
            ZCONTACTJID,
            ZVCARDNAME,
            ZVCARDSTRING
        FROM ZWAVCARDMENTION
            INNER JOIN ZWAMEDIAITEM
                ON ZWAVCARDMENTION.ZMEDIAITEM = ZWAMEDIAITEM.Z_PK
            INNER JOIN ZWAMESSAGE
                ON ZWAMEDIAITEM.ZMESSAGE = ZWAMESSAGE.Z_PK
            INNER JOIN ZWACHATSESSION
                ON ZWAMESSAGE.ZCHATSESSION = ZWACHATSESSION.Z_PK
            LEFT JOIN ZWAGROUPMEMBER
                ON ZWAMESSAGE.ZGROUPMEMBER = ZWAGROUPMEMBER.Z_PK
        WHERE 1=1
            {date_filter}
            {chat_filter_include}
            {chat_filter_exclude}
    """
    c.execute(vcard_query)
    contents = c.fetchall()
    total_row_number = len(contents)
    logging.info(f"Processing vCards...(0/{total_row_number})", extra={"clear": True})

    # Create vCards directory
    path = f'{media_folder}/Message/vCards'
    Path(path).mkdir(parents=True, exist_ok=True)

    # Process each vCard
    with tqdm(total=total_row_number, desc="Processing vCards", unit="vcard", leave=False) as pbar:
        for content in contents:
            process_vcard_item(content, path, data)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']
    logging.info(f"Processed {total_row_number} vCards in {convert_time_unit(total_time)}")


def process_vcard_item(content, path, data):
    """Process a single vCard item."""
    file_paths = []
    vcard_names = content["ZVCARDNAME"].split("_$!<Name-Separator>!$_")
    vcard_strings = content["ZVCARDSTRING"].split("_$!<VCard-Separator>!$_")

    # If this is a list of contacts
    if len(vcard_names) > len(vcard_strings):
        vcard_names.pop(0)  # Dismiss the first element, which is the group name

    # Save each vCard file
    for name, vcard_string in zip(vcard_names, vcard_strings):
        file_name = "".join(x for x in name if x.isalnum())
        file_name = file_name.encode('utf-8')[:230].decode('utf-8', 'ignore')
        file_path = os.path.join(path, f"{file_name}.vcf")
        file_paths.append(file_path)

        if not os.path.isfile(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(vcard_string)

    # Create vCard summary and update message
    vcard_summary = "This media include the following vCard file(s):<br>"
    vcard_summary += " | ".join([f'<a href="{htmle(fp)}">{htmle(name)}</a>' for name,
                                fp in zip(vcard_names, file_paths)])

    message = data.get_chat(content["ZCONTACTJID"]).get_message(content["ZMESSAGE"])
    message.data = vcard_summary
    message.mime = "text/x-vcard"
    message.media = True
    message.meta = True
    message.safe = True


def calls(db, data, timezone_offset, filter_chat):
    """Process WhatsApp call records."""
    c = db.cursor()

    # Build filter conditions
    chat_filter_include = get_chat_condition(
        filter_chat[0], True, ["ZGROUPCALLCREATORUSERJIDSTRING"], None, "ios")
    chat_filter_exclude = get_chat_condition(
        filter_chat[1], False, ["ZGROUPCALLCREATORUSERJIDSTRING"], None, "ios")

    # Get call count
    call_count_query = f"""
        SELECT count()
        FROM ZWACDCALLEVENT
        WHERE 1=1
            {chat_filter_include}
            {chat_filter_exclude}
    """
    c.execute(call_count_query)
    total_row_number = c.fetchone()[0]
    if total_row_number == 0:
        return

    # Fetch call records
    calls_query = f"""
        SELECT ZCALLIDSTRING,
            ZGROUPCALLCREATORUSERJIDSTRING,
            ZGROUPJIDSTRING,
            ZDATE,
            ZOUTCOME,
            ZBYTESRECEIVED + ZBYTESSENT AS bytes_transferred,
            ZDURATION,
            ZVIDEO,
            ZMISSED,
            ZINCOMING
        FROM ZWACDCALLEVENT
            INNER JOIN ZWAAGGREGATECALLEVENT
                ON ZWACDCALLEVENT.Z1CALLEVENTS = ZWAAGGREGATECALLEVENT.Z_PK
        WHERE 1=1
            {chat_filter_include}
            {chat_filter_exclude}
    """
    c.execute(calls_query)

    # Create calls chat
    chat = ChatStore(Device.ANDROID, "WhatsApp Calls")

    with tqdm(total=total_row_number, desc="Processing calls", unit="call", leave=False) as pbar:
        while (content := c.fetchone()) is not None:
            process_call_record(content, chat, data, timezone_offset)
            pbar.update(1)
        total_time = pbar.format_dict['elapsed']

    # Add calls chat to data
    data.add_chat("000000000000000", chat)
    logging.info(f"Processed {total_row_number} calls in {convert_time_unit(total_time)}")


def process_call_record(content, chat, data, timezone_offset):
    """Process a single call record."""
    ts = APPLE_TIME + int(content["ZDATE"])
    call = Message(
        from_me=content["ZINCOMING"] == 0,
        timestamp=ts,
        time=ts,
        key_id=content["ZCALLIDSTRING"],
        timezone_offset=timezone_offset
    )

    # Set sender info
    _jid = content["ZGROUPCALLCREATORUSERJIDSTRING"]
    name = data.get_chat(_jid).name if _jid in data else None
    if _jid is not None and "@" in _jid:
        fallback = _jid.split('@')[0]
    else:
        fallback = None
    call.sender = name or fallback

    # Set call metadata
    call.meta = True
    call.data = format_call_data(call, content)

    # Add call to chat
    chat.add_message(call.key_id, call)


def format_call_data(call, content):
    """Format call data message based on call attributes."""
    # Basic call info
    call_data = (
        f"A {'group ' if content['ZGROUPJIDSTRING'] is not None else ''}"
        f"{'video' if content['ZVIDEO'] == 1 else 'voice'} "
        f"call {'to' if call.from_me else 'from'} "
        f"{call.sender} was "
    )

    # Call outcome
    if content['ZOUTCOME'] in (1, 4):
        call_data += "not answered." if call.from_me else "missed."
    elif content['ZOUTCOME'] == 2:
        call_data += "failed."
    elif content['ZOUTCOME'] == 0:
        call_time = convert_time_unit(int(content['ZDURATION']))
        call_bytes = bytes_to_readable(content['bytes_transferred'])
        call_data += (
            f"initiated and lasted for {call_time} "
            f"with {call_bytes} data transferred."
        )
    else:
        call_data += "in an unknown state."

    return call_data
