import os
import discord
from discord.ext import commands
import sqlite3
from datetime import datetime
from threading import Thread
from flask import Flask


# =========================================================
# 基本設定
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

DB_FILE = "fenhong.db"

MANAGER_ROLE_NAME = "分贓管理員"


# =========================================================
# 日期 / 金額工具
# =========================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return datetime.now().strftime("%Y-%m-%d")


def display_date(date_text):

    if not date_text:
        return ""

    try:
        dt = datetime.strptime(
            date_text,
            "%Y-%m-%d"
        )

        return dt.strftime(
            "%Y/%m/%d"
        )

    except ValueError:
        return date_text


def parse_money(text: str):

    if text is None:
        return None

    text = (
        text.strip()
        .replace(",", "")
        .replace(" ", "")
    )

    if not text:
        return None

    try:

        if text.endswith("億"):

            return round(
                float(text[:-1])
                * 100_000_000
            )

        if text.endswith("萬"):

            return round(
                float(text[:-1])
                * 10_000
            )

        return round(
            float(text)
        )

    except ValueError:
        return None


def format_money(value):

    if value is None:
        return "-"

    return f"{round(float(value)):,}"


def format_mentions(
    user_ids,
    per_line=5
):

    if not user_ids:
        return "尚無"

    mentions = [
        f"<@{user_id}>"
        for user_id in user_ids
    ]

    lines = []

    for index in range(
        0,
        len(mentions),
        per_line
    ):

        lines.append(
            "、".join(
                mentions[
                    index:index + per_line
                ]
            )
        )

    return "\n".join(
        lines
    )


def format_participants(
    participants,
    per_line=5
):

    if not participants:
        return "目前尚無人加入"

    user_ids = [
        user_id
        for user_id, user_name
        in participants
    ]

    return format_mentions(
        user_ids,
        per_line
    )


# =========================================================
# 資料庫
# =========================================================

def db_connect():

    return sqlite3.connect(
        DB_FILE
    )


def ensure_column(
    cursor,
    table_name,
    column_name,
    definition
):

    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    columns = [
        row[1]
        for row in cursor.fetchall()
    ]

    if column_name not in columns:

        cursor.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {definition}
            """
        )


def init_db():

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            UNIQUE(guild_id, team_name)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN'
        )
    """)

    ensure_column(
        cursor,
        "sessions",
        "record_date",
        "TEXT"
    )

    cursor.execute("""
        UPDATE sessions
        SET record_date = substr(created_at, 1, 10)
        WHERE
            record_date IS NULL
            OR record_date = ''
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            UNIQUE(session_id, user_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            market_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT '待處理',
            created_at TEXT NOT NULL
        )
    """)

    ensure_column(
        cursor,
        "items",
        "handling_type",
        "TEXT"
    )

    ensure_column(
        cursor,
        "items",
        "buyer_user_id",
        "INTEGER"
    )

    ensure_column(
        cursor,
        "items",
        "buyer_user_name",
        "TEXT"
    )

    ensure_column(
        cursor,
        "items",
        "sale_price",
        "REAL"
    )

    ensure_column(
        cursor,
        "items",
        "distributable_amount",
        "REAL"
    )

    ensure_column(
        cursor,
        "items",
        "eligible_count",
        "INTEGER"
    )

    ensure_column(
        cursor,
        "items",
        "per_person_amount",
        "REAL"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            UNIQUE(item_id, user_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_receipts (
            item_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            received INTEGER NOT NULL DEFAULT 0,
            received_at TEXT,
            confirmed_by INTEGER,
            PRIMARY KEY(item_id, user_id)
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# 權限
# =========================================================

def has_manager_role(member):

    if not isinstance(
        member,
        discord.Member
    ):
        return False

    return any(
        role.name == MANAGER_ROLE_NAME
        for role in member.roles
    )


def is_manager(interaction):

    member = interaction.user

    if not isinstance(
        member,
        discord.Member
    ):
        return False

    if member.guild_permissions.administrator:
        return True

    if member.guild_permissions.manage_guild:
        return True

    if has_manager_role(
        member
    ):
        return True

    return False


# =========================================================
# 團隊
# =========================================================

def add_team(
    guild_id,
    team_name
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO teams (
                guild_id,
                team_name
            )
            VALUES (?, ?)
            """,
            (
                guild_id,
                team_name
            )
        )

        conn.commit()
        success = True

    except sqlite3.IntegrityError:

        success = False

    conn.close()

    return success


def delete_team(
    guild_id,
    team_name
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM teams
        WHERE
            guild_id = ?
            AND team_name = ?
        """,
        (
            guild_id,
            team_name
        )
    )

    success = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return success


def get_teams(
    guild_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT team_name
        FROM teams
        WHERE guild_id = ?
        ORDER BY id
        """,
        (
            guild_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return [
        row[0]
        for row in rows
    ]


# =========================================================
# 分贓紀錄
# =========================================================

def create_record(
    guild_id,
    team_name,
    channel_id,
    created_by
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO sessions (
            guild_id,
            team_name,
            channel_id,
            created_by,
            created_at,
            record_date,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            guild_id,
            team_name,
            channel_id,
            created_by,
            now_text(),
            today_text()
        )
    )

    record_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return record_id


def delete_record(
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM item_receipts
        WHERE session_id = ?
        """,
        (
            record_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM item_shares
        WHERE session_id = ?
        """,
        (
            record_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM items
        WHERE session_id = ?
        """,
        (
            record_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM participants
        WHERE session_id = ?
        """,
        (
            record_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM sessions
        WHERE id = ?
        """,
        (
            record_id,
        )
    )

    conn.commit()
    conn.close()


def get_record(
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            guild_id,
            team_name,
            channel_id,
            message_id,
            created_by,
            created_at,
            status,
            record_date
        FROM sessions
        WHERE id = ?
        """,
        (
            record_id,
        )
    )

    row = cursor.fetchone()

    conn.close()

    return row


def set_record_message_location(
    record_id,
    channel_id,
    message_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE sessions
        SET
            channel_id = ?,
            message_id = ?
        WHERE id = ?
        """,
        (
            channel_id,
            message_id,
            record_id
        )
    )

    conn.commit()
    conn.close()


def get_open_records():

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            message_id
        FROM sessions
        WHERE
            status = 'OPEN'
            AND message_id IS NOT NULL
        """
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def can_manage_record(
    interaction,
    record_id
):

    record = get_record(
        record_id
    )

    if not record:
        return False

    if interaction.user.id == record[5]:
        return True

    if is_manager(
        interaction
    ):
        return True

    return False


# =========================================================
# 參加者
# =========================================================

def add_participant(
    record_id,
    user_id,
    user_name
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO participants (
                session_id,
                user_id,
                user_name,
                joined_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                record_id,
                user_id,
                user_name,
                now_text()
            )
        )

        conn.commit()
        success = True

    except sqlite3.IntegrityError:

        success = False

    conn.close()

    return success


def remove_participant(
    record_id,
    user_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM participants
        WHERE
            session_id = ?
            AND user_id = ?
        """,
        (
            record_id,
            user_id
        )
    )

    success = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return success


def get_participants(
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            user_name
        FROM participants
        WHERE session_id = ?
        ORDER BY id
        """,
        (
            record_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


# =========================================================
# 寶物
# =========================================================

def add_item(
    record_id,
    item_name,
    market_price
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO items (
            session_id,
            item_name,
            market_price,
            status,
            created_at
        )
        VALUES (?, ?, ?, '待處理', ?)
        """,
        (
            record_id,
            item_name,
            market_price,
            now_text()
        )
    )

    conn.commit()
    conn.close()


def get_items(
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            item_name,
            market_price,
            status,
            handling_type,
            buyer_user_id,
            buyer_user_name,
            sale_price,
            distributable_amount,
            eligible_count,
            per_person_amount
        FROM items
        WHERE session_id = ?
        ORDER BY id
        """,
        (
            record_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_item(
    item_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            session_id,
            item_name,
            market_price,
            status,
            handling_type,
            buyer_user_id,
            buyer_user_name,
            sale_price,
            distributable_amount,
            eligible_count,
            per_person_amount
        FROM items
        WHERE id = ?
        """,
        (
            item_id,
        )
    )

    row = cursor.fetchone()

    conn.close()

    return row


def delete_item(
    item_id,
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM item_receipts
        WHERE item_id = ?
        """,
        (
            item_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM item_shares
        WHERE item_id = ?
        """,
        (
            item_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM items
        WHERE
            id = ?
            AND session_id = ?
        """,
        (
            item_id,
            record_id
        )
    )

    success = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return success


def get_pending_items_with_numbers(
    record_id
):

    items = get_items(
        record_id
    )

    result = []

    for number, item in enumerate(
        items,
        start=1
    ):

        if item[3] == "待處理":

            result.append(
                (
                    number,
                    item
                )
            )

    return result


def get_processed_items_with_numbers(
    record_id
):

    items = get_items(
        record_id
    )

    result = []

    for number, item in enumerate(
        items,
        start=1
    ):

        if item[4] in (
            "INTERNAL",
            "EXTERNAL"
        ):

            result.append(
                (
                    number,
                    item
                )
            )

    return result


def record_has_processed_items(
    record_id
):

    return bool(
        get_processed_items_with_numbers(
            record_id
        )
    )


# =========================================================
# 分紅
# =========================================================

def distribute_amount(
    total_amount,
    members
):

    if not members:
        return []

    total_amount = int(
        round(total_amount)
    )

    count = len(
        members
    )

    base = (
        total_amount // count
    )

    remainder = (
        total_amount % count
    )

    result = []

    for index, (
        user_id,
        user_name
    ) in enumerate(
        members
    ):

        amount = base

        if index < remainder:
            amount += 1

        result.append(
            (
                user_id,
                user_name,
                amount
            )
        )

    return result


def save_item_shares(
    item_id,
    record_id,
    shares
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM item_receipts
        WHERE item_id = ?
        """,
        (
            item_id,
        )
    )

    cursor.execute(
        """
        DELETE FROM item_shares
        WHERE item_id = ?
        """,
        (
            item_id,
        )
    )

    for (
        user_id,
        user_name,
        amount
    ) in shares:

        cursor.execute(
            """
            INSERT INTO item_shares (
                item_id,
                session_id,
                user_id,
                user_name,
                amount
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_id,
                record_id,
                user_id,
                user_name,
                amount
            )
        )

        cursor.execute(
            """
            INSERT INTO item_receipts (
                item_id,
                session_id,
                user_id,
                user_name,
                received
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                item_id,
                record_id,
                user_id,
                user_name
            )
        )

    conn.commit()
    conn.close()


def get_item_payout_rows(
    item_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            s.user_id,
            s.user_name,
            s.amount,
            COALESCE(r.received, 0),
            r.received_at
        FROM item_shares s

        LEFT JOIN item_receipts r
        ON
            r.item_id = s.item_id
            AND r.user_id = s.user_id

        WHERE s.item_id = ?

        ORDER BY s.id
        """,
        (
            item_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_user_item_share(
    item_id,
    user_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            s.amount,
            COALESCE(r.received, 0),
            r.received_at
        FROM item_shares s

        LEFT JOIN item_receipts r
        ON
            r.item_id = s.item_id
            AND r.user_id = s.user_id

        WHERE
            s.item_id = ?
            AND s.user_id = ?
        """,
        (
            item_id,
            user_id
        )
    )

    row = cursor.fetchone()

    conn.close()

    return row


def get_user_unreceived_items(
    record_id,
    user_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            i.id,
            i.item_name,
            s.amount
        FROM item_shares s

        JOIN items i
        ON i.id = s.item_id

        LEFT JOIN item_receipts r
        ON
            r.item_id = s.item_id
            AND r.user_id = s.user_id

        WHERE
            s.session_id = ?
            AND s.user_id = ?
            AND COALESCE(r.received, 0) = 0

        ORDER BY i.id
        """,
        (
            record_id,
            user_id
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def set_item_received(
    item_id,
    record_id,
    user_id,
    user_name
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE item_receipts
        SET
            received = 1,
            received_at = ?,
            confirmed_by = ?,
            user_name = ?
        WHERE
            item_id = ?
            AND user_id = ?
            AND session_id = ?
        """,
        (
            now_text(),
            user_id,
            user_name,
            item_id,
            user_id,
            record_id
        )
    )

    conn.commit()
    conn.close()


def get_item_progress(
    item_id
):

    rows = get_item_payout_rows(
        item_id
    )

    total = len(
        rows
    )

    received = sum(
        1
        for row in rows
        if row[3]
    )

    return (
        received,
        total
    )


def get_item_people_status(
    item_id
):

    rows = get_item_payout_rows(
        item_id
    )

    received_ids = []
    pending_ids = []

    for row in rows:

        user_id = row[0]

        if bool(row[3]):

            received_ids.append(
                user_id
            )

        else:

            pending_ids.append(
                user_id
            )

    received_text = (
        format_mentions(
            received_ids,
            per_line=5
        )
        if received_ids
        else "尚無"
    )

    pending_text = (
        format_mentions(
            pending_ids,
            per_line=5
        )
        if pending_ids
        else "無"
    )

    return (
        received_text,
        pending_text
    )


def item_is_complete(
    item_id
):

    received, total = (
        get_item_progress(
            item_id
        )
    )

    return (
        total > 0
        and received == total
    )


def get_record_progress(
    record_id
):

    items = get_items(
        record_id
    )

    processed = [
        item
        for item in items
        if item[4] in (
            "INTERNAL",
            "EXTERNAL"
        )
    ]

    completed = [
        item
        for item in processed
        if item_is_complete(
            item[0]
        )
    ]

    pending = [
        item
        for item in items
        if item[3] == "待處理"
    ]

    return {
        "total": len(items),
        "processed": len(processed),
        "completed": len(completed),
        "pending": len(pending)
    }


# =========================================================
# 寶物處理
# =========================================================

def process_internal_item(
    item_id,
    buyer_user_id,
    buyer_user_name,
    purchase_price,
    eligible_count,
    per_person_amount
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE items
        SET
            handling_type = 'INTERNAL',
            buyer_user_id = ?,
            buyer_user_name = ?,
            sale_price = ?,
            distributable_amount = ?,
            eligible_count = ?,
            per_person_amount = ?,
            status = '團內購買｜待分紅'
        WHERE id = ?
        """,
        (
            buyer_user_id,
            buyer_user_name,
            purchase_price,
            purchase_price,
            eligible_count,
            per_person_amount,
            item_id
        )
    )

    conn.commit()
    conn.close()


def process_external_item(
    item_id,
    sale_price,
    distributable_amount,
    eligible_count,
    per_person_amount
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE items
        SET
            handling_type = 'EXTERNAL',
            buyer_user_id = NULL,
            buyer_user_name = NULL,
            sale_price = ?,
            distributable_amount = ?,
            eligible_count = ?,
            per_person_amount = ?,
            status = '已售出｜待分紅'
        WHERE id = ?
        """,
        (
            sale_price,
            distributable_amount,
            eligible_count,
            per_person_amount,
            item_id
        )
    )

    conn.commit()
    conn.close()


# =========================================================
# 移除參加者 + 重新計算
# =========================================================

def remove_participant_and_recalculate(
    record_id,
    removed_user_id
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            DELETE FROM participants
            WHERE
                session_id = ?
                AND user_id = ?
            """,
            (
                record_id,
                removed_user_id
            )
        )

        if cursor.rowcount <= 0:

            conn.rollback()
            conn.close()

            return False

        cursor.execute(
            """
            SELECT
                user_id,
                user_name
            FROM participants
            WHERE session_id = ?
            ORDER BY id
            """,
            (
                record_id,
            )
        )

        participants = (
            cursor.fetchall()
        )

        cursor.execute(
            """
            SELECT
                id,
                market_price,
                handling_type,
                buyer_user_id,
                buyer_user_name,
                sale_price,
                distributable_amount
            FROM items
            WHERE
                session_id = ?
                AND handling_type IN (
                    'INTERNAL',
                    'EXTERNAL'
                )
            ORDER BY id
            """,
            (
                record_id,
            )
        )

        processed_items = (
            cursor.fetchall()
        )

        for item in processed_items:

            (
                item_id,
                market_price,
                handling_type,
                buyer_user_id,
                buyer_user_name,
                saved_sale_price,
                saved_distributable_amount
            ) = item

            # =================================================
            # 被移除者就是團內購買者
            # → 該件寶物退回待處理
            # =================================================

            if (
                handling_type == "INTERNAL"
                and buyer_user_id
                == removed_user_id
            ):

                cursor.execute(
                    """
                    DELETE FROM item_receipts
                    WHERE item_id = ?
                    """,
                    (
                        item_id,
                    )
                )

                cursor.execute(
                    """
                    DELETE FROM item_shares
                    WHERE item_id = ?
                    """,
                    (
                        item_id,
                    )
                )

                cursor.execute(
                    """
                    UPDATE items
                    SET
                        status = '待處理',
                        handling_type = NULL,
                        buyer_user_id = NULL,
                        buyer_user_name = NULL,
                        sale_price = NULL,
                        distributable_amount = NULL,
                        eligible_count = NULL,
                        per_person_amount = NULL
                    WHERE id = ?
                    """,
                    (
                        item_id,
                    )
                )

                continue

            # =================================================
            # 團內購買
            # =================================================

            if handling_type == "INTERNAL":

                if saved_sale_price is not None:

                    purchase_price = int(
                        round(
                            saved_sale_price
                        )
                    )

                else:

                    purchase_price = int(
                        round(
                            market_price
                            * 0.80
                        )
                    )

                recipients = [
                    participant
                    for participant
                    in participants
                    if participant[0]
                    != buyer_user_id
                ]

                eligible_count = len(
                    recipients
                )

                if eligible_count > 0:

                    shares = distribute_amount(
                        purchase_price,
                        recipients
                    )

                    per_person_amount = int(
                        round(
                            purchase_price
                            / eligible_count
                        )
                    )

                else:

                    shares = []
                    per_person_amount = 0

                cursor.execute(
                    """
                    UPDATE items
                    SET
                        sale_price = ?,
                        distributable_amount = ?,
                        eligible_count = ?,
                        per_person_amount = ?,
                        status = '團內購買｜待分紅'
                    WHERE id = ?
                    """,
                    (
                        purchase_price,
                        purchase_price,
                        eligible_count,
                        per_person_amount,
                        item_id
                    )
                )

            # =================================================
            # 對外販售
            # =================================================

            elif handling_type == "EXTERNAL":

                if saved_sale_price is not None:

                    sale_price = int(
                        round(
                            saved_sale_price
                        )
                    )

                else:

                    sale_price = int(
                        round(
                            market_price
                        )
                    )

                fee = int(
                    round(
                        sale_price
                        * 0.06
                    )
                )

                distributable_amount = (
                    sale_price
                    - fee
                )

                recipients = list(
                    participants
                )

                eligible_count = len(
                    recipients
                )

                if eligible_count > 0:

                    shares = distribute_amount(
                        distributable_amount,
                        recipients
                    )

                    per_person_amount = int(
                        round(
                            distributable_amount
                            / eligible_count
                        )
                    )

                else:

                    shares = []
                    per_person_amount = 0

                cursor.execute(
                    """
                    UPDATE items
                    SET
                        sale_price = ?,
                        distributable_amount = ?,
                        eligible_count = ?,
                        per_person_amount = ?,
                        status = '已售出｜待分紅'
                    WHERE id = ?
                    """,
                    (
                        sale_price,
                        distributable_amount,
                        eligible_count,
                        per_person_amount,
                        item_id
                    )
                )

            else:

                continue

            # =================================================
            # 清除舊分紅
            # =================================================

            cursor.execute(
                """
                DELETE FROM item_receipts
                WHERE item_id = ?
                """,
                (
                    item_id,
                )
            )

            cursor.execute(
                """
                DELETE FROM item_shares
                WHERE item_id = ?
                """,
                (
                    item_id,
                )
            )

            # =================================================
            # 建立新分紅
            # =================================================

            for (
                share_user_id,
                share_user_name,
                share_amount
            ) in shares:

                cursor.execute(
                    """
                    INSERT INTO item_shares (
                        item_id,
                        session_id,
                        user_id,
                        user_name,
                        amount
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        record_id,
                        share_user_id,
                        share_user_name,
                        share_amount
                    )
                )

                cursor.execute(
                    """
                    INSERT INTO item_receipts (
                        item_id,
                        session_id,
                        user_id,
                        user_name,
                        received,
                        received_at,
                        confirmed_by
                    )
                    VALUES (
                        ?, ?, ?, ?, 0, NULL, NULL
                    )
                    """,
                    (
                        item_id,
                        record_id,
                        share_user_id,
                        share_user_name
                    )
                )

        conn.commit()
        conn.close()

        return True

    except Exception as e:

        conn.rollback()
        conn.close()

        print(
            "移除參加者並重新計算失敗：",
            repr(e)
        )

        return False


# =========================================================
# Render 免費 Web Service
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():

    return (
        "柏青哥分贓助手運作中",
        200
    )


@web_app.route("/health")
def health():

    return (
        "OK",
        200
    )


def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    web_app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# =========================================================
# BOT
# =========================================================

intents = discord.Intents.default()

intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# 主卡
# =========================================================

def build_record_embed(
    record_id
):

    record = get_record(
        record_id
    )

    if not record:

        return discord.Embed(
            title="找不到紀錄"
        )

    team_name = record[2]

    record_date = record[8]

    participants = get_participants(
        record_id
    )

    items = get_items(
        record_id
    )

    progress = get_record_progress(
        record_id
    )

    date_display = display_date(
        record_date
    )

    # =====================================================
    # 狀態
    # =====================================================

    if (
        progress["total"] > 0
        and
        progress["completed"]
        == progress["total"]
    ):

        status_text = (
            "✅ 全部分紅完成"
        )

        embed_color = (
            discord.Color.green()
        )

    elif progress["processed"] > 0:

        status_text = (
            "💵 分紅進行中"
        )

        embed_color = (
            discord.Color.blue()
        )

    else:

        status_text = (
            "🟢 進行中"
        )

        embed_color = (
            discord.Color.light_grey()
        )

    # =====================================================
    # 主標題
    # =====================================================

    embed = discord.Embed(
        title=(
            f"💰 柏青哥｜{team_name}"
        ),
        description=(
            f"📅 **{date_display}**"
            f"｜{status_text}"
        ),
        color=embed_color
    )

    # =====================================================
    # 參與人員
    # =====================================================

    embed.add_field(
        name=(
            f"👥 參與｜{len(participants)} 人"
        ),
        value=(
            format_participants(
                participants,
                per_line=5
            )
        ),
        inline=False
    )

    # =====================================================
    # 沒有寶物
    # =====================================================

    if not items:

        embed.add_field(
            name="🎁 寶物",
            value="目前尚未新增寶物",
            inline=False
        )

    # =====================================================
    # 每件寶物
    # =====================================================

    for index, item in enumerate(
        items,
        start=1
    ):

        (
            item_id,
            item_name,
            market_price,
            item_status,
            handling_type,
            buyer_user_id,
            buyer_user_name,
            sale_price,
            distributable_amount,
            eligible_count,
            per_person_amount
        ) = item

        item_title = (
            f"🎁 {index:02d}｜{item_name}"
        )

        # =================================================
        # 待處理
        # =================================================

        if handling_type not in (
            "INTERNAL",
            "EXTERNAL"
        ):

            item_value = (
                f"市價：`{format_money(market_price)}`"
                f"｜🟡 **待處理**\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        # =================================================
        # 團內購買
        # =================================================

        elif handling_type == "INTERNAL":

            received, total = (
                get_item_progress(
                    item_id
                )
            )

            received_people, pending_people = (
                get_item_people_status(
                    item_id
                )
            )

            item_value = (
                f"市價：`{format_money(market_price)}`\n"
                f"🔵 團內：`{format_money(sale_price)}`"
                f"｜購買：<@{buyer_user_id}>\n"
                f"👥 分紅：**{eligible_count} 人**"
                f"｜💰 每人：`{format_money(per_person_amount)}`\n"
                f"📦 進度：**{received}/{total}**\n"
                f"✅ 已領：{received_people}\n"
                f"⬜ 未領：{pending_people}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        # =================================================
        # 對外販售
        # =================================================

        else:

            received, total = (
                get_item_progress(
                    item_id
                )
            )

            received_people, pending_people = (
                get_item_people_status(
                    item_id
                )
            )

            fee = (
                sale_price
                - distributable_amount
            )

            item_value = (
                f"市價：`{format_money(market_price)}`\n"
                f"🟢 外售：`{format_money(sale_price)}`"
                f"｜手續費：`{format_money(fee)}`\n"
                f"👥 分紅：**{eligible_count} 人**"
                f"｜💰 每人：`{format_money(per_person_amount)}`\n"
                f"📦 進度：**{received}/{total}**\n"
                f"✅ 已領：{received_people}\n"
                f"⬜ 未領：{pending_people}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        # =================================================
        # Discord 單一 field 上限
        # =================================================

        if len(item_value) <= 1024:

            embed.add_field(
                name=item_title,
                value=item_value,
                inline=False
            )

        else:

            first_part = (
                item_value[:1000]
            )

            second_part = (
                item_value[1000:]
            )

            embed.add_field(
                name=item_title,
                value=first_part,
                inline=False
            )

            if second_part:

                embed.add_field(
                    name=(
                        f"{item_title}｜續"
                    ),
                    value=second_part[:1024],
                    inline=False
                )

    # =====================================================
    # 今日總進度
    # =====================================================

    embed.add_field(
        name="📊 今日進度",
        value=(
            f"寶物 **{progress['total']}**"
            f"｜已處理 **{progress['processed']}**"
            f"｜待處理 **{progress['pending']}**"
            f"｜完成 **{progress['completed']}/{progress['total']}**"
        ),
        inline=False
    )

    embed.set_footer(
        text=(
            f"{team_name}｜{date_display}"
        )
    )

    return embed


# =========================================================
# 更新主卡
# =========================================================

async def refresh_record_message(
    record_id
):

    record = get_record(
        record_id
    )

    if not record:
        return

    channel_id = record[3]

    message_id = record[4]

    if not message_id:
        return

    channel = bot.get_channel(
        channel_id
    )

    if channel is None:

        try:

            channel = await bot.fetch_channel(
                channel_id
            )

        except Exception:

            return

    try:

        message = (
            channel.get_partial_message(
                message_id
            )
        )

        await message.edit(
            embed=build_record_embed(
                record_id
            ),
            view=RecordView(
                record_id
            )
        )

    except Exception as e:

        print(
            "更新主卡失敗：",
            repr(e)
        )


# =========================================================
# 新增寶物 Modal
# =========================================================

class AddItemModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title="🎁 新增寶物"
        )

        self.record_id = record_id

        self.name_input = (
            discord.ui.TextInput(
                placeholder="例如：永恆魔導士帽",
                required=True,
                max_length=100
            )
        )

        self.price_input = (
            discord.ui.TextInput(
                placeholder="例如：1,600,000,000",
                required=True,
                max_length=30
            )
        )

        self.add_item(
            discord.ui.Label(
                text="寶物名稱",
                component=self.name_input
            )
        )

        self.add_item(
            discord.ui.Label(
                text="目前拍賣最低價",
                component=self.price_input
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        item_name = (
            self.name_input.value.strip()
        )

        market_price = parse_money(
            self.price_input.value
        )

        if (
            not item_name
            or market_price is None
            or market_price <= 0
        ):

            await interaction.response.defer()
            return

        add_item(
            self.record_id,
            item_name,
            market_price
        )

        await interaction.response.defer()

        await refresh_record_message(
            self.record_id
        )


# =========================================================
# 移除參加者 Modal
# =========================================================

class RemoveParticipantModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title="👤 移除參加者"
        )

        self.record_id = record_id

        participants = get_participants(
            record_id
        )

        options = []

        for index, (
            user_id,
            user_name
        ) in enumerate(
            participants[:25],
            start=1
        ):

            options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {user_name}"
                    )[:100],
                    value=str(
                        user_id
                    )
                )
            )

        self.member_select = (
            discord.ui.Select(
                placeholder="選擇要移除的人員",
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="選擇參加者",
                description="移除後會重新計算分紅",
                component=self.member_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        user_id = int(
            self.member_select.values[0]
        )

        success = (
            remove_participant_and_recalculate(
                self.record_id,
                user_id
            )
        )

        await interaction.response.defer()

        if not success:
            return

        await refresh_record_message(
            self.record_id
        )


# =========================================================
# 處理寶物 Modal
# =========================================================

class ProcessItemModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title="⚙️ 處理寶物"
        )

        self.record_id = record_id

        # =================================================
        # 寶物
        # =================================================

        pending_items = (
            get_pending_items_with_numbers(
                record_id
            )
        )

        item_options = []

        for (
            number,
            item
        ) in pending_items[:25]:

            item_options.append(
                discord.SelectOption(
                    label=(
                        f"{number}. {item[1]}"
                    )[:100],
                    value=str(
                        item[0]
                    ),
                    description=(
                        f"市價："
                        f"{format_money(item[2])}"
                    )[:100]
                )
            )

        self.item_select = (
            discord.ui.Select(
                placeholder="選擇要處理的寶物",
                options=item_options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="選擇寶物",
                component=self.item_select
            )
        )

        # =================================================
        # 處理方式
        # =================================================

        self.type_select = (
            discord.ui.Select(
                placeholder="選擇處理方式",
                options=[
                    discord.SelectOption(
                        label="🔵 團內購買",
                        value="INTERNAL",
                        description="拍賣最低價 × 80%"
                    ),
                    discord.SelectOption(
                        label="🟢 對外販售",
                        value="EXTERNAL",
                        description="拍賣價格扣除 6% 手續費"
                    )
                ],
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="處理方式",
                component=self.type_select
            )
        )

        # =================================================
        # 購買者
        # =================================================

        participants = get_participants(
            record_id
        )

        buyer_options = []

        for index, (
            user_id,
            user_name
        ) in enumerate(
            participants[:25],
            start=1
        ):

            buyer_options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {user_name}"
                    )[:100],
                    value=str(
                        user_id
                    )
                )
            )

        self.buyer_select = (
            discord.ui.Select(
                placeholder="團內購買時選擇購買者",
                options=buyer_options,
                min_values=0,
                max_values=1,
                required=False
            )
        )

        self.add_item(
            discord.ui.Label(
                text="購買者",
                description=(
                    "團內購買才需要選；"
                    "對外販售不用選"
                ),
                component=self.buyer_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        item_id = int(
            self.item_select.values[0]
        )

        process_type = (
            self.type_select.values[0]
        )

        item = get_item(
            item_id
        )

        participants = get_participants(
            self.record_id
        )

        if not item:

            await interaction.response.defer()
            return

        if item[5] in (
            "INTERNAL",
            "EXTERNAL"
        ):

            await interaction.response.defer()
            return

        if not participants:

            await interaction.response.defer()
            return

        # =================================================
        # 團內購買
        # =================================================

        if process_type == "INTERNAL":

            if len(participants) < 2:

                await interaction.response.defer()
                return

            if not self.buyer_select.values:

                await interaction.response.defer()
                return

            buyer_user_id = int(
                self.buyer_select.values[0]
            )

            buyer_user_name = None

            for (
                user_id,
                user_name
            ) in participants:

                if user_id == buyer_user_id:

                    buyer_user_name = user_name
                    break

            if buyer_user_name is None:

                await interaction.response.defer()
                return

            recipients = [
                participant
                for participant in participants
                if participant[0]
                != buyer_user_id
            ]

            if not recipients:

                await interaction.response.defer()
                return

            market_price = item[3]

            purchase_price = round(
                market_price
                * 0.80
            )

            shares = distribute_amount(
                purchase_price,
                recipients
            )

            eligible_count = len(
                recipients
            )

            per_person_amount = round(
                purchase_price
                / eligible_count
            )

            process_internal_item(
                item_id,
                buyer_user_id,
                buyer_user_name,
                purchase_price,
                eligible_count,
                per_person_amount
            )

            save_item_shares(
                item_id,
                self.record_id,
                shares
            )

            await interaction.response.defer()

            await refresh_record_message(
                self.record_id
            )

            return

        # =================================================
        # 對外販售
        # =================================================

        if process_type == "EXTERNAL":

            sale_price = round(
                item[3]
            )

            fee = round(
                sale_price
                * 0.06
            )

            distributable_amount = (
                sale_price
                - fee
            )

            shares = distribute_amount(
                distributable_amount,
                participants
            )

            eligible_count = len(
                participants
            )

            per_person_amount = round(
                distributable_amount
                / eligible_count
            )

            process_external_item(
                item_id,
                sale_price,
                distributable_amount,
                eligible_count,
                per_person_amount
            )

            save_item_shares(
                item_id,
                self.record_id,
                shares
            )

            await interaction.response.defer()

            await refresh_record_message(
                self.record_id
            )

            return


# =========================================================
# 刪除寶物 Modal
# =========================================================

class DeleteItemModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title="🗑️ 刪除寶物"
        )

        self.record_id = record_id

        items = get_items(
            record_id
        )

        options = []

        for index, item in enumerate(
            items[:25],
            start=1
        ):

            options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {item[1]}"
                    )[:100],
                    value=str(
                        item[0]
                    ),
                    description=(
                        f"市價："
                        f"{format_money(item[2])}"
                    )[:100]
                )
            )

        self.item_select = (
            discord.ui.Select(
                placeholder="選擇要刪除的寶物",
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="選擇寶物",
                component=self.item_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        item_id = int(
            self.item_select.values[0]
        )

        delete_item(
            item_id,
            self.record_id
        )

        await interaction.response.defer()

        await refresh_record_message(
            self.record_id
        )


# =========================================================
# 我已收到 Modal
# =========================================================

class MyReceiptModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id,
        user_id
    ):

        super().__init__(
            title="✅ 我已收到"
        )

        self.record_id = record_id

        rows = get_user_unreceived_items(
            record_id,
            user_id
        )

        options = []

        for index, row in enumerate(
            rows[:25],
            start=1
        ):

            options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {row[1]}"
                    )[:100],
                    value=str(
                        row[0]
                    ),
                    description=(
                        f"應領："
                        f"{format_money(row[2])}"
                    )[:100]
                )
            )

        self.item_select = (
            discord.ui.Select(
                placeholder="選擇你已收到的寶物",
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="選擇寶物",
                component=self.item_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        item_id = int(
            self.item_select.values[0]
        )

        share = get_user_item_share(
            item_id,
            interaction.user.id
        )

        if not share:

            await interaction.response.defer()
            return

        if share[1]:

            await interaction.response.defer()
            return

        set_item_received(
            item_id,
            self.record_id,
            interaction.user.id,
            interaction.user.display_name
        )

        await interaction.response.defer()

        await refresh_record_message(
            self.record_id
        )


# =========================================================
# 主卡按鈕
# =========================================================

class RecordView(
    discord.ui.View
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            timeout=None
        )

        self.record_id = record_id

    # =====================================================
    # 第一排
    # =====================================================

    @discord.ui.button(
        label="我有參加",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=0,
        custom_id="fenhong_join"
    )
    async def join_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if record_has_processed_items(
            self.record_id
        ):

            await interaction.response.defer()
            return

        success = add_participant(
            self.record_id,
            interaction.user.id,
            interaction.user.display_name
        )

        if not success:

            await interaction.response.defer()
            return

        await interaction.response.edit_message(
            embed=build_record_embed(
                self.record_id
            ),
            view=self
        )

    @discord.ui.button(
        label="取消參加",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="fenhong_leave"
    )
    async def leave_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if record_has_processed_items(
            self.record_id
        ):

            await interaction.response.defer()
            return

        success = remove_participant(
            self.record_id,
            interaction.user.id
        )

        if not success:

            await interaction.response.defer()
            return

        await interaction.response.edit_message(
            embed=build_record_embed(
                self.record_id
            ),
            view=self
        )

    # =====================================================
    # 第二排
    # =====================================================

    @discord.ui.button(
        label="新增寶物",
        emoji="🎁",
        style=discord.ButtonStyle.primary,
        row=1,
        custom_id="fenhong_add"
    )
    async def add_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            AddItemModal(
                self.record_id
            )
        )

    @discord.ui.button(
        label="處理寶物",
        emoji="⚙️",
        style=discord.ButtonStyle.primary,
        row=1,
        custom_id="fenhong_process"
    )
    async def process_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        if not get_pending_items_with_numbers(
            self.record_id
        ):

            await interaction.response.defer()
            return

        if not get_participants(
            self.record_id
        ):

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            ProcessItemModal(
                self.record_id
            )
        )

    @discord.ui.button(
        label="刪除寶物",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=1,
        custom_id="fenhong_delete"
    )
    async def delete_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        if not get_items(
            self.record_id
        ):

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            DeleteItemModal(
                self.record_id
            )
        )

    # =====================================================
    # 第三排
    # =====================================================

    @discord.ui.button(
        label="移除參加者",
        emoji="👤",
        style=discord.ButtonStyle.danger,
        row=2,
        custom_id="fenhong_remove_participant"
    )
    async def remove_participant_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        participants = get_participants(
            self.record_id
        )

        if not participants:

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            RemoveParticipantModal(
                self.record_id
            )
        )

    @discord.ui.button(
        label="我已收到",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=2,
        custom_id="fenhong_received"
    )
    async def received_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        rows = get_user_unreceived_items(
            self.record_id,
            interaction.user.id
        )

        if not rows:

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            MyReceiptModal(
                self.record_id,
                interaction.user.id
            )
        )


# =========================================================
# 新增團隊 Modal
# =========================================================

class AddTeamModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title="➕ 新增團隊"
        )

        self.guild_id = guild_id

        self.team_input = (
            discord.ui.TextInput(
                placeholder="例如：龍王2團",
                required=True,
                max_length=50
            )
        )

        self.add_item(
            discord.ui.Label(
                text="團隊名稱",
                component=self.team_input
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        team_name = (
            self.team_input.value.strip()
        )

        if not team_name:

            await interaction.response.defer()
            return

        add_team(
            self.guild_id,
            team_name
        )

        await interaction.response.defer()


# =========================================================
# 刪除團隊 Modal
# =========================================================

class DeleteTeamModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title="🗑️ 刪除團隊"
        )

        self.guild_id = guild_id

        teams = get_teams(
            guild_id
        )

        options = []

        for index, team in enumerate(
            teams[:25],
            start=1
        ):

            options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {team}"
                    )[:100],
                    value=team
                )
            )

        self.team_select = (
            discord.ui.Select(
                placeholder="選擇要刪除的團隊",
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="選擇團隊",
                component=self.team_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        team_name = (
            self.team_select.values[0]
        )

        delete_team(
            self.guild_id,
            team_name
        )

        await interaction.response.defer()


# =========================================================
# 查看團隊 Modal
# =========================================================

class ViewTeamsModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title="👥 查看團隊"
        )

        teams = get_teams(
            guild_id
        )

        options = []

        for index, team in enumerate(
            teams[:25],
            start=1
        ):

            options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {team}"
                    )[:100],
                    value=team
                )
            )

        self.team_select = (
            discord.ui.Select(
                placeholder="目前所有團隊",
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text=(
                    f"目前共有 {len(teams)} 個團隊"
                ),
                component=self.team_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer()


# =========================================================
# 開團 Modal
# =========================================================

class OpenTeamModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title="💰 開啟分贓"
        )

        self.guild_id = guild_id

        teams = get_teams(
            guild_id
        )

        options = []

        for index, team in enumerate(
            teams[:25],
            start=1
        ):

            options.append(
                discord.SelectOption(
                    label=(
                        f"{index}. {team}"
                    )[:100],
                    value=team
                )
            )

        self.team_select = (
            discord.ui.Select(
                placeholder="選擇團隊",
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text="選擇團隊",
                description=(
                    f"日期："
                    f"{display_date(today_text())}"
                ),
                component=self.team_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        team_name = (
            self.team_select.values[0]
        )

        record_id = create_record(
            self.guild_id,
            team_name,
            interaction.channel.id,
            interaction.user.id
        )

        await interaction.response.defer()

        try:

            main_message = (
                await interaction.channel.send(
                    embed=build_record_embed(
                        record_id
                    ),
                    view=RecordView(
                        record_id
                    )
                )
            )

            set_record_message_location(
                record_id,
                interaction.channel.id,
                main_message.id
            )

        except discord.Forbidden as e:

            delete_record(
                record_id
            )

            print(
                "建立主卡失敗：Bot 缺少頻道權限",
                repr(e)
            )

        except Exception as e:

            delete_record(
                record_id
            )

            print(
                "建立主卡錯誤：",
                repr(e)
            )


# =========================================================
# Slash 指令
# =========================================================

@bot.tree.command(
    name="新增團隊",
    description="新增團隊"
)
async def add_team_command(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.defer()
        return

    if not is_manager(
        interaction
    ):

        await interaction.response.defer()
        return

    await interaction.response.send_modal(
        AddTeamModal(
            interaction.guild.id
        )
    )


@bot.tree.command(
    name="刪除團隊",
    description="刪除團隊"
)
async def delete_team_command(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.defer()
        return

    if not is_manager(
        interaction
    ):

        await interaction.response.defer()
        return

    teams = get_teams(
        interaction.guild.id
    )

    if not teams:

        await interaction.response.defer()
        return

    await interaction.response.send_modal(
        DeleteTeamModal(
            interaction.guild.id
        )
    )


@bot.tree.command(
    name="查看團隊",
    description="查看目前所有團隊"
)
async def view_teams_command(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.defer()
        return

    teams = get_teams(
        interaction.guild.id
    )

    if not teams:

        await interaction.response.defer()
        return

    await interaction.response.send_modal(
        ViewTeamsModal(
            interaction.guild.id
        )
    )


@bot.tree.command(
    name="開團",
    description="開啟新的分贓紀錄"
)
async def open_team_command(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.defer()
        return

    teams = get_teams(
        interaction.guild.id
    )

    if not teams:

        await interaction.response.defer()
        return

    await interaction.response.send_modal(
        OpenTeamModal(
            interaction.guild.id
        )
    )


# =========================================================
# BOT 上線
# =========================================================

views_restored = False


@bot.event
async def on_ready():

    global views_restored

    try:

        synced = await bot.tree.sync()

        if not views_restored:

            for (
                record_id,
                message_id
            ) in get_open_records():

                try:

                    bot.add_view(
                        RecordView(
                            record_id
                        ),
                        message_id=message_id
                    )

                except Exception as e:

                    print(
                        "恢復按鈕失敗：",
                        record_id,
                        repr(e)
                    )

            views_restored = True

        print(
            "=============================="
        )

        print(
            f"Bot 已登入：{bot.user}"
        )

        print(
            f"Slash 指令同步完成：{len(synced)} 個"
        )

        print(
            f"管理身分組：{MANAGER_ROLE_NAME}"
        )

        print(
            "Render Web Service：已啟動"
        )

        print(
            "=============================="
        )

    except Exception as e:

        print(
            "啟動錯誤：",
            repr(e)
        )


# =========================================================
# 啟動
# =========================================================

if __name__ == "__main__":

    init_db()

    if not TOKEN:

        raise RuntimeError(
            "找不到 DISCORD_TOKEN，"
            "請確認 Render Environment Variables 已設定。"
        )

    web_thread = Thread(
        target=run_web_server,
        daemon=True
    )

    web_thread.start()

    bot.run(TOKEN)