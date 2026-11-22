



import discord
from discord.ext import commands, tasks
import sqlite3
import upstash_sqlite
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo
import asyncio
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

TOKEN = os.getenv('DISCORD_TOKEN')
DB_FILE = 'fenhong.db'
MANAGER_ROLE_NAME = '分贓管理員'


# =========================================================
# Render Web Service HTTP 健康檢查（標準函式庫，無需 Flask）
# =========================================================

class RenderHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/', '/health'):
            data = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        else:
            data = b'{"error":"not found"}'
            self.send_response(404)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # 避免 Upstash 每次 ping 都產生大量日誌。
        pass


def start_render_health_server():
    port = int(os.environ.get('PORT', '10000'))
    server = ThreadingHTTPServer(('0.0.0.0', port), RenderHealthHandler)
    Thread(target=server.serve_forever, name='render-health', daemon=True).start()
    print(f'Render HTTP /health 已啟動，監聽 0.0.0.0:{port}')



def now_text():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today_text():
    return datetime.now().strftime('%Y-%m-%d')


def display_date(date_text):
    if not date_text:
        return ''

    try:
        dt = datetime.strptime(
            date_text,
            '%Y-%m-%d'
        )

        return dt.strftime('%Y/%m/%d')

    except ValueError:
        return date_text


def parse_money(text):
    if text is None:
        return None

    text = (
        str(text)
        .strip()
        .replace(',', '')
        .replace('，', '')
        .replace(' ', '')
    )

    if not text:
        return None

    try:

        if text.endswith('億'):
            return round(
                float(text[:-1])
                * 100000000
            )

        if text.endswith('萬'):
            return round(
                float(text[:-1])
                * 10000
            )

        return round(
            float(text)
        )

    except ValueError:
        return None


def format_money(value):
    if value is None:
        return '-'

    return f'{round(float(value)):,}'


def format_mentions(
    user_ids,
    per_line=5
):
    if not user_ids:
        return '尚無'

    mentions = [
        f'<@{user_id}>'
        for user_id in user_ids
    ]

    lines = []

    for index in range(
        0,
        len(mentions),
        per_line
    ):
        lines.append(
            '、'.join(
                mentions[
                    index:index + per_line
                ]
            )
        )

    return '\n'.join(lines)


def format_participants(
    participants,
    per_line=5
):
    if not participants:
        return '目前尚無人加入'

    user_ids = [
        user_id
        for user_id, user_name
        in participants
    ]

    return format_mentions(
        user_ids,
        per_line
    )


def db_connect():
    return upstash_sqlite.connect(DB_FILE)


def ensure_column(
    cursor,
    table_name,
    column_name,
    definition
):
    cursor.execute(
        f'PRAGMA table_info({table_name})'
    )

    columns = [
        row[1]
        for row in cursor.fetchall()
    ]

    if column_name not in columns:

        cursor.execute(
            f'ALTER TABLE {table_name} '
            f'ADD COLUMN {column_name} {definition}'
        )


def init_db():

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            UNIQUE(guild_id, team_name)
        )
        '''
    )

    cursor.execute(
        '''
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
        '''
    )

    ensure_column(
        cursor,
        'sessions',
        'record_date',
        'TEXT'
    )

    cursor.execute(
        '''
        UPDATE sessions
        SET record_date = substr(created_at, 1, 10)
        WHERE
            record_date IS NULL
            OR record_date = ''
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            UNIQUE(session_id, user_id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            market_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT '待處理',
            created_at TEXT NOT NULL
        )
        '''
    )

    ensure_column(
        cursor,
        'items',
        'handling_type',
        'TEXT'
    )

    ensure_column(
        cursor,
        'items',
        'buyer_user_id',
        'INTEGER'
    )

    ensure_column(
        cursor,
        'items',
        'buyer_user_name',
        'TEXT'
    )

    ensure_column(
        cursor,
        'items',
        'sale_price',
        'REAL'
    )

    ensure_column(
        cursor,
        'items',
        'distributable_amount',
        'REAL'
    )

    ensure_column(
        cursor,
        'items',
        'eligible_count',
        'INTEGER'
    )

    ensure_column(
        cursor,
        'items',
        'per_person_amount',
        'REAL'
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS item_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            UNIQUE(item_id, user_id)
        )
        '''
    )

    cursor.execute(
        '''
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
        '''
    )

    conn.commit()
    conn.close()


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

    if has_manager_role(member):
        return True

    return False


def add_team(
    guild_id,
    team_name
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            '''
            INSERT INTO teams (
                guild_id,
                team_name
            )
            VALUES (?, ?)
            ''',
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
        '''
        DELETE FROM teams
        WHERE
            guild_id = ?
            AND team_name = ?
        ''',
        (
            guild_id,
            team_name
        )
    )

    success = (
        cursor.rowcount > 0
    )

    conn.commit()
    conn.close()

    return success


def get_teams(
    guild_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT team_name
        FROM teams
        WHERE guild_id = ?
        ORDER BY id
        ''',
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


def create_record(
    guild_id,
    team_name,
    channel_id,
    created_by
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
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
        ''',
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
        '''
        DELETE FROM item_receipts
        WHERE session_id = ?
        ''',
        (
            record_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM item_shares
        WHERE session_id = ?
        ''',
        (
            record_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM items
        WHERE session_id = ?
        ''',
        (
            record_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM participants
        WHERE session_id = ?
        ''',
        (
            record_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM sessions
        WHERE id = ?
        ''',
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
        '''
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
        ''',
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
        '''
        UPDATE sessions
        SET
            channel_id = ?,
            message_id = ?
        WHERE id = ?
        ''',
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
        '''
        SELECT
            id,
            message_id
        FROM sessions
        WHERE
            status = 'OPEN'
            AND message_id IS NOT NULL
        '''
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

    if (
        interaction.user.id
        == record[5]
    ):
        return True

    if is_manager(
        interaction
    ):
        return True

    return False


def add_participant(
    record_id,
    user_id,
    user_name
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            '''
            INSERT INTO participants (
                session_id,
                user_id,
                user_name,
                joined_at
            )
            VALUES (?, ?, ?, ?)
            ''',
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


def add_participants_bulk(
    record_id,
    members
):

    if not members:
        return 0

    conn = db_connect()
    cursor = conn.cursor()

    added_count = 0

    try:

        for member in members:

            if getattr(
                member,
                'bot',
                False
            ):
                continue

            cursor.execute(
                '''
                INSERT OR IGNORE INTO participants (
                    session_id,
                    user_id,
                    user_name,
                    joined_at
                )
                VALUES (?, ?, ?, ?)
                ''',
                (
                    record_id,
                    member.id,
                    member.display_name,
                    now_text()
                )
            )

            if cursor.rowcount > 0:
                added_count += 1

        conn.commit()

    except Exception as e:

        conn.rollback()

        print(
            '批次加入參加者失敗：',
            repr(e)
        )

    conn.close()

    return added_count


def remove_participant(
    record_id,
    user_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
        DELETE FROM participants
        WHERE
            session_id = ?
            AND user_id = ?
        ''',
        (
            record_id,
            user_id
        )
    )

    success = (
        cursor.rowcount > 0
    )

    conn.commit()
    conn.close()

    return success


def get_participants(
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT
            user_id,
            user_name
        FROM participants
        WHERE session_id = ?
        ORDER BY id
        ''',
        (
            record_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


async def fetch_all_guild_members(
    guild
):

    if guild is None:
        return []

    members_by_id = {}

    try:

        print(
            '正在向 Discord 抓取完整伺服器成員...'
        )

        async for member in guild.fetch_members(
            limit=None
        ):

            if member.bot:
                continue

            members_by_id[
                member.id
            ] = member

        print(
            f'Discord 完整成員抓取完成：'
            f'{len(members_by_id)} 人'
        )

    except Exception as e:

        print(
            '抓取完整 Discord 成員失敗：',
            repr(e)
        )

        for member in guild.members:

            if member.bot:
                continue

            members_by_id[
                member.id
            ] = member

    members = list(
        members_by_id.values()
    )

    members.sort(
        key=lambda member: (
            (
                member.display_name
                or member.name
            ).lower(),
            member.id
        )
    )

    return members


def get_auto_voice_members(
    guild,
    team_name
):

    if guild is None:
        return []

    member_dict = {}
    found_channel = None

    for voice_channel in guild.voice_channels:

        if voice_channel.name != team_name:
            continue

        found_channel = voice_channel

        for member in voice_channel.members:

            if member.bot:
                continue

            member_dict[
                member.id
            ] = member

        break

    members = list(
        member_dict.values()
    )

    members.sort(
        key=lambda member: (
            (
                member.display_name
                or member.name
            ).lower(),
            member.id
        )
    )

    if found_channel is None:

        print(
            f'語音頻道自動帶入：'
            f'找不到 [{team_name}]'
        )

    else:

        print(
            f'語音頻道自動帶入：'
            f'[{team_name}]'
            f'｜共 {len(members)} 人'
        )

    return members


def add_items_bulk(
    record_id,
    item_rows
):

    if not item_rows:
        return 0

    conn = db_connect()
    cursor = conn.cursor()

    added_count = 0

    try:

        for (
            item_name,
            market_price
        ) in item_rows:

            cursor.execute(
                '''
                INSERT INTO items (
                    session_id,
                    item_name,
                    market_price,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, '待處理', ?)
                ''',
                (
                    record_id,
                    item_name,
                    market_price,
                    now_text()
                )
            )

            added_count += 1

        conn.commit()

    except Exception as e:

        conn.rollback()
        added_count = 0

        print(
            '批次新增寶物失敗：',
            repr(e)
        )

    conn.close()

    return added_count


def get_items(
    record_id
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
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
        ''',
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
        '''
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
        ''',
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
        '''
        DELETE FROM item_receipts
        WHERE item_id = ?
        ''',
        (
            item_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM item_shares
        WHERE item_id = ?
        ''',
        (
            item_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM items
        WHERE
            id = ?
            AND session_id = ?
        ''',
        (
            item_id,
            record_id
        )
    )

    success = (
        cursor.rowcount > 0
    )

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

        if item[3] == '待處理':
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
            'INTERNAL',
            'EXTERNAL'
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


def distribute_amount(
    total_amount,
    members
):

    if not members:
        return []

    total_amount = int(
        round(
            total_amount
        )
    )

    count = len(
        members
    )

    base = (
        total_amount
        // count
    )

    remainder = (
        total_amount
        % count
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
        '''
        DELETE FROM item_receipts
        WHERE item_id = ?
        ''',
        (
            item_id,
        )
    )

    cursor.execute(
        '''
        DELETE FROM item_shares
        WHERE item_id = ?
        ''',
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
            '''
            INSERT INTO item_shares (
                item_id,
                session_id,
                user_id,
                user_name,
                amount
            )
            VALUES (?, ?, ?, ?, ?)
            ''',
            (
                item_id,
                record_id,
                user_id,
                user_name,
                amount
            )
        )

        cursor.execute(
            '''
            INSERT INTO item_receipts (
                item_id,
                session_id,
                user_id,
                user_name,
                received
            )
            VALUES (?, ?, ?, ?, 0)
            ''',
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
        '''
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
        ''',
        (
            item_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_item_unreceived_rows(
    item_id
):

    return [
        row
        for row
        in get_item_payout_rows(
            item_id
        )
        if not row[3]
    ]


def set_item_received(
    item_id,
    record_id,
    user_id,
    user_name,
    confirmed_by
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        '''
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
            AND received = 0
        ''',
        (
            now_text(),
            confirmed_by,
            user_name,
            item_id,
            user_id,
            record_id
        )
    )

    changed = (
        cursor.rowcount > 0
    )

    conn.commit()
    conn.close()

    return changed


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

        if bool(
            row[3]
        ):
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
        else '尚無'
    )

    pending_text = (
        format_mentions(
            pending_ids,
            per_line=5
        )
        if pending_ids
        else '無'
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
            'INTERNAL',
            'EXTERNAL'
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
        if item[3] == '待處理'
    ]

    return {
        'total': len(items),
        'processed': len(processed),
        'completed': len(completed),
        'pending': len(pending)
    }


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
        '''
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
        ''',
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
        '''
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
        ''',
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


def update_market_price_and_recalculate(
    item_id,
    record_id,
    new_market_price
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            '''
            SELECT
                id,
                session_id,
                market_price,
                status,
                handling_type,
                buyer_user_id,
                buyer_user_name
            FROM items
            WHERE
                id = ?
                AND session_id = ?
            ''',
            (
                item_id,
                record_id
            )
        )

        item = cursor.fetchone()

        if not item:

            conn.rollback()
            conn.close()

            return False

        handling_type = item[4]
        buyer_user_id = item[5]
        buyer_user_name = item[6]

        new_market_price = int(
            round(
                new_market_price
            )
        )

        if new_market_price <= 0:

            conn.rollback()
            conn.close()

            return False

        cursor.execute(
            '''
            UPDATE items
            SET market_price = ?
            WHERE
                id = ?
                AND session_id = ?
            ''',
            (
                new_market_price,
                item_id,
                record_id
            )
        )

        if handling_type not in (
            'INTERNAL',
            'EXTERNAL'
        ):

            conn.commit()
            conn.close()

            return True

        cursor.execute(
            '''
            SELECT
                user_id,
                user_name
            FROM participants
            WHERE session_id = ?
            ORDER BY id
            ''',
            (
                record_id,
            )
        )

        participants = cursor.fetchall()

        cursor.execute(
            '''
            DELETE FROM item_receipts
            WHERE item_id = ?
            ''',
            (
                item_id,
            )
        )

        cursor.execute(
            '''
            DELETE FROM item_shares
            WHERE item_id = ?
            ''',
            (
                item_id,
            )
        )

        if handling_type == 'INTERNAL':

            purchase_price = int(
                round(
                    new_market_price
                    * 0.8
                )
            )

            recipients = [
                participant
                for participant in participants
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
                '''
                UPDATE items
                SET
                    market_price = ?,
                    sale_price = ?,
                    distributable_amount = ?,
                    eligible_count = ?,
                    per_person_amount = ?,
                    status = '團內購買｜待分紅'
                WHERE id = ?
                ''',
                (
                    new_market_price,
                    purchase_price,
                    purchase_price,
                    eligible_count,
                    per_person_amount,
                    item_id
                )
            )

        else:

            sale_price = (
                new_market_price
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
                '''
                UPDATE items
                SET
                    market_price = ?,
                    sale_price = ?,
                    distributable_amount = ?,
                    eligible_count = ?,
                    per_person_amount = ?,
                    status = '已售出｜待分紅'
                WHERE id = ?
                ''',
                (
                    new_market_price,
                    sale_price,
                    distributable_amount,
                    eligible_count,
                    per_person_amount,
                    item_id
                )
            )

        for (
            share_user_id,
            share_user_name,
            share_amount
        ) in shares:

            cursor.execute(
                '''
                INSERT INTO item_shares (
                    item_id,
                    session_id,
                    user_id,
                    user_name,
                    amount
                )
                VALUES (?, ?, ?, ?, ?)
                ''',
                (
                    item_id,
                    record_id,
                    share_user_id,
                    share_user_name,
                    share_amount
                )
            )

            cursor.execute(
                '''
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
                ''',
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
            '修改市價並重新計算失敗：',
            repr(e)
        )

        return False


def remove_participant_and_recalculate(
    record_id,
    removed_user_id
):

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            '''
            DELETE FROM participants
            WHERE
                session_id = ?
                AND user_id = ?
            ''',
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
            '''
            SELECT
                user_id,
                user_name
            FROM participants
            WHERE session_id = ?
            ORDER BY id
            ''',
            (
                record_id,
            )
        )

        participants = cursor.fetchall()

        cursor.execute(
            '''
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
            ''',
            (
                record_id,
            )
        )

        processed_items = cursor.fetchall()

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

            if (
                handling_type == 'INTERNAL'
                and
                buyer_user_id == removed_user_id
            ):

                cursor.execute(
                    '''
                    DELETE FROM item_receipts
                    WHERE item_id = ?
                    ''',
                    (
                        item_id,
                    )
                )

                cursor.execute(
                    '''
                    DELETE FROM item_shares
                    WHERE item_id = ?
                    ''',
                    (
                        item_id,
                    )
                )

                cursor.execute(
                    '''
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
                    ''',
                    (
                        item_id,
                    )
                )

                continue

            if handling_type == 'INTERNAL':

                purchase_price = int(
                    round(
                        saved_sale_price
                        if saved_sale_price
                        is not None
                        else market_price * 0.8
                    )
                )

                recipients = [
                    participant
                    for participant in participants
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
                    '''
                    UPDATE items
                    SET
                        sale_price = ?,
                        distributable_amount = ?,
                        eligible_count = ?,
                        per_person_amount = ?,
                        status = '團內購買｜待分紅'
                    WHERE id = ?
                    ''',
                    (
                        purchase_price,
                        purchase_price,
                        eligible_count,
                        per_person_amount,
                        item_id
                    )
                )

            elif handling_type == 'EXTERNAL':

                sale_price = int(
                    round(
                        saved_sale_price
                        if saved_sale_price
                        is not None
                        else market_price
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
                    '''
                    UPDATE items
                    SET
                        sale_price = ?,
                        distributable_amount = ?,
                        eligible_count = ?,
                        per_person_amount = ?,
                        status = '已售出｜待分紅'
                    WHERE id = ?
                    ''',
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

            cursor.execute(
                '''
                DELETE FROM item_receipts
                WHERE item_id = ?
                ''',
                (
                    item_id,
                )
            )

            cursor.execute(
                '''
                DELETE FROM item_shares
                WHERE item_id = ?
                ''',
                (
                    item_id,
                )
            )

            for (
                share_user_id,
                share_user_name,
                share_amount
            ) in shares:

                cursor.execute(
                    '''
                    INSERT INTO item_shares (
                        item_id,
                        session_id,
                        user_id,
                        user_name,
                        amount
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (
                        item_id,
                        record_id,
                        share_user_id,
                        share_user_name,
                        share_amount
                    )
                )

                cursor.execute(
                    '''
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
                    ''',
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
            '移除參加者並重新計算失敗：',
            repr(e)
        )

        return False


intents = discord.Intents.default()

intents.members = True
intents.voice_states = True


bot = commands.Bot(
    command_prefix='!',
    intents=intents
)


def build_record_embed(
    record_id
):

    record = get_record(
        record_id
    )

    if not record:

        return discord.Embed(
            title='找不到紀錄'
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

    if (
        progress['total'] > 0
        and
        progress['completed']
        == progress['total']
    ):

        status_text = (
            '✅ 全部分紅完成'
        )

        embed_color = (
            discord.Color.green()
        )

    elif progress['processed'] > 0:

        status_text = (
            '💵 分紅進行中'
        )

        embed_color = (
            discord.Color.blue()
        )

    else:

        status_text = (
            '🟢 進行中'
        )

        embed_color = (
            discord.Color.light_grey()
        )

    embed = discord.Embed(
        title=(
            f'💰 柏青哥｜{team_name}'
        ),
        description=(
            f'📅 **{date_display}**'
            f'｜{status_text}'
        ),
        color=embed_color
    )

    embed.add_field(
        name=(
            f'👥 參與｜'
            f'{len(participants)} 人'
        ),
        value=(
            format_participants(
                participants,
                per_line=5
            )
        ),
        inline=False
    )

    if not items:

        embed.add_field(
            name='🎁 寶物',
            value='目前尚未新增寶物',
            inline=False
        )

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

        status_mark = ''

        if handling_type in (
            'INTERNAL',
            'EXTERNAL'
        ):

            unreceived_rows = (
                get_item_unreceived_rows(
                    item_id
                )
            )

            if unreceived_rows:
                status_mark = '🟨 '

            else:
                status_mark = '✅ '

        if index == 1:

            item_title = (
                f'▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅\n'
                f'{status_mark}'
                f'🎁 {index:02d}'
                f'｜{item_name}'
            )

        else:

            item_title = (
                f'{status_mark}'
                f'🎁 {index:02d}'
                f'｜{item_name}'
            )

        if handling_type not in (
            'INTERNAL',
            'EXTERNAL'
        ):

            item_value = (
                f'市價：'
                f'`{format_money(market_price)}`'
                f'｜🟡 **待處理**\n'
               f'▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅'
            )

        elif handling_type == 'INTERNAL':

            received, total = (
                get_item_progress(
                    item_id
                )
            )

            (
                received_people,
                pending_people
            ) = get_item_people_status(
                item_id
            )

            item_value = (
                f'市價：'
                f'`{format_money(market_price)}`\n'
                f'🔵 團內：'
                f'`{format_money(sale_price)}`'
                f'｜購買：'
                f'<@{buyer_user_id}>\n'
                f'👥 分紅：'
                f'**{eligible_count} 人**'
                f'｜💰 每人：'
                f'`{format_money(per_person_amount)}`\n'
                f'📦 進度：'
                f'**{received}/{total}**\n'
                f'✅ 已領：'
                f'{received_people}\n\n\n'
                f'⬜ 未領：'
                f'{pending_people}\n'
               f'▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅'
            )

        else:

            received, total = (
                get_item_progress(
                    item_id
                )
            )

            (
                received_people,
                pending_people
            ) = get_item_people_status(
                item_id
            )

            fee = (
                sale_price
                - distributable_amount
            )

            item_value = (
                f'市價：'
                f'`{format_money(market_price)}`\n'
                f'🟢 外售：'
                f'`{format_money(sale_price)}`'
                f'｜手續費：'
                f'`{format_money(fee)}`\n'
                f'👥 分紅：'
                f'**{eligible_count} 人**'
                f'｜💰 每人：'
                f'`{format_money(per_person_amount)}`\n'
                f'📦 進度：'
                f'**{received}/{total}**\n'
                f'✅ 已領：'
                f'{received_people}\n\n\n'
                f'⬜ 未領：'
                f'{pending_people}\n'
            f'▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅'
            )

        embed.add_field(
            name=item_title,
            value=item_value[:1024],
            inline=False
        )

    embed.add_field(
        name='📊 今日進度',
        value=(
            f"寶物 **{progress['total']}**"
            f"｜已處理 **{progress['processed']}**"
            f"｜待處理 **{progress['pending']}**"
            f"｜完成 **{progress['completed']}/"
            f"{progress['total']}**"
        ),
        inline=False
    )

    embed.set_footer(
        text=(
            f'{team_name}'
            f'｜{date_display}'
        )
    )

    return embed


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

        except Exception as e:

            print(
                f'找不到頻道 {channel_id}：',
                repr(e)
            )

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
            '更新主卡失敗：',
            record_id,
            repr(e)
        )


class AddItemModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title='🎁 新增寶物'
        )

        self.record_id = record_id

        self.item1_name = (
            discord.ui.TextInput(
                placeholder='例如：暴風雪30',
                required=True,
                max_length=100
            )
        )

        self.item1_price = (
            discord.ui.TextInput(
                placeholder=(
                    '例如：3.6億 / '
                    '8500萬 / '
                    '120,000,000'
                ),
                required=True,
                max_length=30
            )
        )

        self.item2_name = (
            discord.ui.TextInput(
                placeholder='第2筆寶物名稱，可留空',
                required=False,
                max_length=100
            )
        )

        self.item2_price = (
            discord.ui.TextInput(
                placeholder='第2筆金額，可留空',
                required=False,
                max_length=30
            )
        )

        self.add_item(
            discord.ui.Label(
                text='寶物1名稱',
                component=self.item1_name
            )
        )

        self.add_item(
            discord.ui.Label(
                text='寶物1金額',
                component=self.item1_price
            )
        )

        self.add_item(
            discord.ui.Label(
                text='寶物2名稱',
                component=self.item2_name
            )
        )

        self.add_item(
            discord.ui.Label(
                text='寶物2金額',
                component=self.item2_price
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        items_to_add = []

        item1_name = (
            self.item1_name.value.strip()
        )

        item1_price = parse_money(
            self.item1_price.value
        )

        if (
            not item1_name
            or item1_price is None
            or item1_price <= 0
        ):

            await interaction.response.defer()
            return

        items_to_add.append(
            (
                item1_name,
                item1_price
            )
        )

        item2_name = (
            self.item2_name.value.strip()
        )

        item2_price_text = (
            self.item2_price.value.strip()
        )

        if (
            item2_name
            and item2_price_text
        ):

            item2_price = parse_money(
                item2_price_text
            )

            if (
                item2_price is None
                or item2_price <= 0
            ):

                await interaction.response.defer()
                return

            items_to_add.append(
                (
                    item2_name,
                    item2_price
                )
            )

        elif (
            item2_name
            or item2_price_text
        ):

            await interaction.response.defer()
            return

        added_count = add_items_bulk(
            self.record_id,
            items_to_add
        )

        await interaction.response.defer()

        if added_count <= 0:
            return

        await refresh_record_message(
            self.record_id
        )


class EditMarketPriceModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title='💲 修改市價'
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
                        f'{index}. '
                        f'{item[1]}'
                    )[:100],
                    value=str(
                        item[0]
                    ),
                    description=(
                        f'目前市價：'
                        f'{format_money(item[2])}'
                    )[:100]
                )
            )

        self.item_select = (
            discord.ui.Select(
                placeholder='選擇要修改市價的寶物',
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.new_price_input = (
            discord.ui.TextInput(
                placeholder=(
                    '例如：3.6億 / '
                    '8500萬 / '
                    '120,000,000'
                ),
                required=True,
                max_length=30
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇寶物',
                component=self.item_select
            )
        )

        self.add_item(
            discord.ui.Label(
                text='新的市價',
                description=(
                    '處理過的寶物會重新計算，'
                    '領取紀錄會重置'
                ),
                component=self.new_price_input
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        item_id = int(
            self.item_select.values[0]
        )

        new_market_price = parse_money(
            self.new_price_input.value
        )

        if (
            new_market_price is None
            or new_market_price <= 0
        ):

            await interaction.response.defer()
            return

        success = (
            update_market_price_and_recalculate(
                item_id,
                self.record_id,
                new_market_price
            )
        )

        await interaction.response.defer()

        if not success:
            return

        print(
            f'修改市價：'
            f'寶物ID {item_id}'
            f'｜新市價 '
            f'{format_money(new_market_price)}'
        )

        await refresh_record_message(
            self.record_id
        )


class ManualAddParticipantModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id,
        members
    ):

        super().__init__(
            title='➕ 手動加入參加者'
        )

        self.record_id = record_id
        self.members = members
        self.member_selects = []

        usable_members = (
            members[:125]
        )

        chunks = [
            usable_members[
                i:i + 25
            ]
            for i in range(
                0,
                len(usable_members),
                25
            )
        ]

        for index, chunk in enumerate(
            chunks,
            start=1
        ):

            options = []

            for member in chunk:

                display_name = (
                    member.display_name
                    or member.name
                )

                options.append(
                    discord.SelectOption(
                        label=display_name[:100],
                        description=(
                            f'@{member.name}'
                        )[:100],
                        value=str(
                            member.id
                        )
                    )
                )

            start_number = (
                (index - 1)
                * 25
                + 1
            )

            end_number = (
                start_number
                + len(chunk)
                - 1
            )

            member_select = (
                discord.ui.Select(
                    placeholder=(
                        f'選擇成員 '
                        f'{start_number}～'
                        f'{end_number}'
                    ),
                    options=options,
                    min_values=0,
                    max_values=len(options),
                    required=False
                )
            )

            self.member_selects.append(
                member_select
            )

            self.add_item(
                discord.ui.Label(
                    text=(
                        f'成員 '
                        f'{start_number}～'
                        f'{end_number}'
                    ),
                    description='可一次選擇多人',
                    component=member_select
                )
            )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        if record_has_processed_items(
            self.record_id
        ):

            await interaction.response.defer()
            return

        selected_ids = set()

        for member_select in (
            self.member_selects
        ):

            for value in (
                member_select.values
            ):

                selected_ids.add(
                    int(value)
                )

        if not selected_ids:

            await interaction.response.defer()
            return

        member_map = {
            member.id: member
            for member in self.members
        }

        added_count = 0

        for member_id in selected_ids:

            member = member_map.get(
                member_id
            )

            if member is None:
                continue

            if member.bot:
                continue

            success = add_participant(
                self.record_id,
                member.id,
                member.display_name
            )

            if success:
                added_count += 1

        await interaction.response.defer()

        if added_count <= 0:
            return

        await refresh_record_message(
            self.record_id
        )


class RemoveParticipantModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title='👤 移除參加者'
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
                        f'{index}. '
                        f'{user_name}'
                    )[:100],
                    value=str(
                        user_id
                    )
                )
            )

        self.member_select = (
            discord.ui.Select(
                placeholder='選擇要移除的人員',
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇參加者',
                description='移除後會重新計算分紅',
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


class ProcessItemModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title='⚙️ 處理寶物'
        )

        self.record_id = record_id

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
                        f'{number}. '
                        f'{item[1]}'
                    )[:100],
                    value=str(
                        item[0]
                    ),
                    description=(
                        f'市價：'
                        f'{format_money(item[2])}'
                    )[:100]
                )
            )

        self.item_select = (
            discord.ui.Select(
                placeholder='選擇要處理的寶物',
                options=item_options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇寶物',
                component=self.item_select
            )
        )

        self.type_select = (
            discord.ui.Select(
                placeholder='選擇處理方式',
                options=[
                    discord.SelectOption(
                        label='🔵 團內購買',
                        value='INTERNAL',
                        description='拍賣最低價 × 80%'
                    ),
                    discord.SelectOption(
                        label='🟢 對外販售',
                        value='EXTERNAL',
                        description='販售價扣除 6% 手續費'
                    )
                ],
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='處理方式',
                component=self.type_select
            )
        )

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
                        f'{index}. '
                        f'{user_name}'
                    )[:100],
                    value=str(
                        user_id
                    )
                )
            )

        self.buyer_select = (
            discord.ui.Select(
                placeholder='團內購買時選擇購買者',
                options=buyer_options,
                min_values=0,
                max_values=1,
                required=False
            )
        )

        self.add_item(
            discord.ui.Label(
                text='購買者',
                description='團內購買才需要選擇',
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
            'INTERNAL',
            'EXTERNAL'
        ):

            await interaction.response.defer()
            return

        if not participants:

            await interaction.response.defer()
            return

        if process_type == 'INTERNAL':

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
                * 0.8
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

        if process_type == 'EXTERNAL':

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


class DeleteItemModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title='🗑️ 刪除寶物'
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
                        f'{index}. '
                        f'{item[1]}'
                    )[:100],
                    value=str(
                        item[0]
                    ),
                    description=(
                        f'市價：'
                        f'{format_money(item[2])}'
                    )[:100]
                )
            )

        self.item_select = (
            discord.ui.Select(
                placeholder='選擇要刪除的寶物',
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇寶物',
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


class ConfirmReceiptModal(
    discord.ui.Modal
):

    def __init__(
        self,
        record_id
    ):

        super().__init__(
            title='✅ 確認領取'
        )

        self.record_id = record_id

        processed_items = (
            get_processed_items_with_numbers(
                record_id
            )
        )

        item_options = []

        for (
            number,
            item
        ) in processed_items:

            item_id = item[0]

            unreceived_rows = (
                get_item_unreceived_rows(
                    item_id
                )
            )

            if not unreceived_rows:
                continue

            item_options.append(
                discord.SelectOption(
                    label=(
                        f'{number}. '
                        f'{item[1]}'
                    )[:100],
                    description=(
                        f'尚未領取：'
                        f'{len(unreceived_rows)} 人'
                    )[:100],
                    value=str(
                        item_id
                    )
                )
            )

            if len(
                item_options
            ) >= 25:
                break

        self.item_select = (
            discord.ui.Select(
                placeholder='選擇寶物',
                options=item_options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇寶物',
                description='選擇要確認領取的寶物',
                component=self.item_select
            )
        )

        selectable_users = {}

        for (
            number,
            item
        ) in processed_items:

            item_id = item[0]

            unreceived_rows = (
                get_item_unreceived_rows(
                    item_id
                )
            )

            for row in unreceived_rows:

                user_id = row[0]
                user_name = row[1]

                if user_id not in selectable_users:

                    selectable_users[
                        user_id
                    ] = user_name

                if len(
                    selectable_users
                ) >= 25:
                    break

            if len(
                selectable_users
            ) >= 25:
                break

        member_options = []

        for (
            user_id,
            user_name
        ) in selectable_users.items():

            member_options.append(
                discord.SelectOption(
                    label=user_name[:100],
                    value=str(
                        user_id
                    )
                )
            )

        self.member_select = (
            discord.ui.Select(
                placeholder='選擇已領取隊員，可複選',
                options=member_options,
                min_values=1,
                max_values=len(member_options),
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇已領取隊員',
                description='可一次選擇多人，最多25人',
                component=self.member_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        if not can_manage_record(
            interaction,
            self.record_id
        ):

            await interaction.response.defer()
            return

        item_id = int(
            self.item_select.values[0]
        )

        selected_user_ids = {
            int(value)
            for value
            in self.member_select.values
        }

        valid_rows = (
            get_item_unreceived_rows(
                item_id
            )
        )

        valid_users = {
            row[0]: row[1]
            for row in valid_rows
        }

        confirmed_count = 0

        for user_id in selected_user_ids:

            user_name = valid_users.get(
                user_id
            )

            if user_name is None:
                continue

            success = set_item_received(
                item_id=item_id,
                record_id=self.record_id,
                user_id=user_id,
                user_name=user_name,
                confirmed_by=interaction.user.id
            )

            if success:
                confirmed_count += 1

        await interaction.response.defer()

        if confirmed_count <= 0:
            return

        await refresh_record_message(
            self.record_id
        )


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

    @discord.ui.button(
        label='我有參加',
        emoji='✅',
        style=discord.ButtonStyle.success,
        row=0,
        custom_id='fenhong_join'
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
        label='取消參加',
        emoji='↩️',
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id='fenhong_leave'
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

    @discord.ui.button(
        label='新增寶物',
        emoji='🎁',
        style=discord.ButtonStyle.primary,
        row=1,
        custom_id='fenhong_add'
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
        label='處理寶物',
        emoji='⚙️',
        style=discord.ButtonStyle.primary,
        row=1,
        custom_id='fenhong_process'
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
        label='修改市價',
        emoji='💲',
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id='fenhong_edit_market_price'
    )
    async def edit_market_price_button(
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
            EditMarketPriceModal(
                self.record_id
            )
        )

    @discord.ui.button(
        label='刪除寶物',
        emoji='🗑️',
        style=discord.ButtonStyle.danger,
        row=1,
        custom_id='fenhong_delete'
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

    @discord.ui.button(
        label='手動加入',
        emoji='➕',
        style=discord.ButtonStyle.primary,
        row=2,
        custom_id='fenhong_manual_add_participant'
    )
    async def manual_add_participant_button(
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

        if record_has_processed_items(
            self.record_id
        ):

            await interaction.response.defer()
            return

        if interaction.guild is None:

            await interaction.response.defer()
            return

        members = await fetch_all_guild_members(
            interaction.guild
        )

        existing_participants = (
            get_participants(
                self.record_id
            )
        )

        existing_ids = {
            user_id
            for user_id, user_name
            in existing_participants
        }

        members = [
            member
            for member in members
            if (
                not member.bot
                and
                member.id not in existing_ids
            )
        ]

        members.sort(
            key=lambda member: (
                (
                    member.display_name
                    or member.name
                ).lower(),
                member.id
            )
        )

        if not members:

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            ManualAddParticipantModal(
                record_id=self.record_id,
                members=members[:125]
            )
        )

    @discord.ui.button(
        label='移除參加者',
        emoji='👤',
        style=discord.ButtonStyle.danger,
        row=2,
        custom_id='fenhong_remove_participant'
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
        label='確認領取',
        emoji='✅',
        style=discord.ButtonStyle.success,
        row=2,
        custom_id='fenhong_received'
    )
    async def confirm_received_button(
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

        processed_items = (
            get_processed_items_with_numbers(
                self.record_id
            )
        )

        if not processed_items:

            await interaction.response.defer()
            return

        has_unreceived = False

        for (
            number,
            item
        ) in processed_items:

            if get_item_unreceived_rows(
                item[0]
            ):

                has_unreceived = True
                break

        if not has_unreceived:

            await interaction.response.defer()
            return

        await interaction.response.send_modal(
            ConfirmReceiptModal(
                self.record_id
            )
        )


class AddTeamModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title='➕ 新增團隊'
        )

        self.guild_id = guild_id

        self.team_input = (
            discord.ui.TextInput(
                placeholder='例如：龍王2團',
                required=True,
                max_length=50
            )
        )

        self.add_item(
            discord.ui.Label(
                text='團隊名稱',
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


class DeleteTeamModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title='🗑️ 刪除團隊'
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
                        f'{index}. '
                        f'{team}'
                    )[:100],
                    value=team
                )
            )

        self.team_select = (
            discord.ui.Select(
                placeholder='選擇要刪除的團隊',
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇團隊',
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


class ViewTeamsModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title='👥 查看團隊'
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
                        f'{index}. '
                        f'{team}'
                    )[:100],
                    value=team
                )
            )

        self.team_select = (
            discord.ui.Select(
                placeholder='目前所有團隊',
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text=(
                    f'目前共有 '
                    f'{len(teams)} 個團隊'
                ),
                component=self.team_select
            )
        )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer()


class OpenTeamModal(
    discord.ui.Modal
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            title='💰 開啟分贓'
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
                        f'{index}. '
                        f'{team}'
                    )[:100],
                    value=team
                )
            )

        self.team_select = (
            discord.ui.Select(
                placeholder='選擇團隊',
                options=options,
                min_values=1,
                max_values=1,
                required=True
            )
        )

        self.add_item(
            discord.ui.Label(
                text='選擇團隊',
                description=(
                    f'日期：'
                    f'{display_date(today_text())}'
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

        await interaction.response.defer()

        if interaction.guild is None:
            return

        # =================================================
        # 優先找與團隊同名的文字頻道
        # 龍王1團 -> #龍王1團
        # =================================================

        target_channel = None

        for channel in (
            interaction.guild.text_channels
        ):

            if channel.name == team_name:

                target_channel = channel
                break

        # =================================================
        # 如果找不到同名文字頻道
        # 就使用目前輸入 /開團 的文字頻道
        # =================================================

        if target_channel is None:

            if isinstance(
                interaction.channel,
                discord.TextChannel
            ):

                target_channel = (
                    interaction.channel
                )

            else:

                print(
                    f'/開團失敗：'
                    f'找不到 #{team_name} 文字頻道'
                )

                return

        # =================================================
        # 建立討論串名稱
        # 例如：
        # 10/21 20:30｜龍王1團
        # =================================================

        thread_name = (
            datetime.now().strftime(
                '%m/%d %H:%M'
            )
            + f'｜{team_name}'
        )

        # =================================================
        # Discord 無法設定討論串永久不封存
        # 依序嘗試最長時間：
        # 7天 -> 3天 -> 1天 -> 1小時
        # =================================================

        preferred_durations = [
            10080,
            4320,
            1440,
            60
        ]

        thread = None
        archive_duration = None
        last_error = None

        for duration in (
            preferred_durations
        ):

            try:

                thread = (
                    await target_channel.create_thread(
                        name=thread_name,
                        type=(
                            discord.ChannelType.public_thread
                        ),
                        auto_archive_duration=duration
                    )
                )

                archive_duration = duration
                break

            except discord.Forbidden as e:

                print(
                    '建立討論串失敗：'
                    'Bot 權限不足',
                    repr(e)
                )

                return

            except discord.HTTPException as e:

                last_error = e
                continue

            except Exception as e:

                print(
                    '建立討論串失敗：',
                    repr(e)
                )

                return

        if thread is None:

            print(
                '建立討論串失敗：',
                repr(last_error)
            )

            return

        print(
            f'建立討論串成功：'
            f'{thread_name}'
            f'｜自動封存：'
            f'{archive_duration} 分鐘'
        )

        # =================================================
        # 每次 /開團 都建立新的資料庫紀錄
        # channel_id 存的是這次討論串 ID
        # =================================================

        record_id = create_record(
            self.guild_id,
            team_name,
            thread.id,
            interaction.user.id
        )

        # =================================================
        # 自動抓同名語音頻道
        # =================================================

        voice_members = (
            get_auto_voice_members(
                interaction.guild,
                team_name
            )
        )

        auto_added_count = (
            add_participants_bulk(
                record_id,
                voice_members
            )
        )

        print(
            f'/開團 [{team_name}] '
            f'自動加入語音成員：'
            f'{auto_added_count} 人'
        )

        # =================================================
        # 分贓主卡送進討論串
        # =================================================

        try:

            main_message = (
                await thread.send(
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
                thread.id,
                main_message.id
            )

            print(
                f'分贓卡建立完成：'
                f'{thread_name}'
            )

        except discord.Forbidden as e:

            delete_record(
                record_id
            )

            print(
                '建立分贓卡失敗：'
                'Bot 無法在討論串發訊息',
                repr(e)
            )

        except Exception as e:

            delete_record(
                record_id
            )

            print(
                '建立分贓卡錯誤：',
                repr(e)
            )


@bot.tree.command(
    name='新增團隊',
    description='新增團隊'
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
    name='刪除團隊',
    description='刪除團隊'
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
    name='查看團隊',
    description='查看目前所有團隊'
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
    name='開團',
    description='開啟新的分贓紀錄'
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


# ============================================================
# 每日打王職業投票：多 BOSS 自動排程 + 手動發布
# 手動可重複發布；自動排程各 BOSS 每日只觸發一次，互不影響
# ============================================================
POLL_GUILD_ID = 1554214082884800604
POLL_CHANNEL_ID = 1561891631513477170
TAIWAN_TZ = ZoneInfo('Asia/Taipei')
POLL_JOBS = ('火', '英雄', '奶媽', '眼', '刀賊', '其他職業')
poll_publish_lock = asyncio.Lock()


def init_boss_poll_tables():
    with db_connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS boss_poll_settings (
            guild_id INTEGER NOT NULL,
            boss_name TEXT NOT NULL,
            schedule_time TEXT,
            boss_time TEXT,
            PRIMARY KEY(guild_id, boss_name)
        )''')
        # 既有資料庫原本只有發布時間；新增獨立的打王時間欄位。
        ensure_column(conn.cursor(), 'boss_poll_settings', 'boss_time', 'TEXT')
        conn.execute('''CREATE TABLE IF NOT EXISTS boss_poll_posts (
            guild_id INTEGER NOT NULL,
            boss_name TEXT NOT NULL,
            poll_date TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            state TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            PRIMARY KEY(guild_id, boss_name, poll_date)
        )''')
        # 舊的 boss_poll_posts 表保留，不再讀寫；避免破壞歷史資料。
        # 新表記錄每一份投票；手動發布不限次數。
        conn.execute('''CREATE TABLE IF NOT EXISTS boss_poll_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            boss_name TEXT NOT NULL,
            poll_date TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        )''')
        # 只有「自動排程」需要防止 30 秒輪詢造成同分鐘重複發送。
        conn.execute('''CREATE TABLE IF NOT EXISTS boss_poll_auto_runs (
            guild_id INTEGER NOT NULL,
            boss_name TEXT NOT NULL,
            poll_date TEXT NOT NULL,
            schedule_time TEXT NOT NULL,
            state TEXT NOT NULL,
            message_id INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, boss_name, poll_date, schedule_time)
        )''')
        # 初次使用時才建立兩場預設投票；已存在設定不被重設。
        conn.execute(
            'INSERT OR IGNORE INTO boss_poll_settings (guild_id, boss_name, schedule_time, boss_time) VALUES (?, ?, ?, ?)',
            (POLL_GUILD_ID, '皮卡丘', '08:00', None)
        )
        conn.execute(
            'INSERT OR IGNORE INTO boss_poll_settings (guild_id, boss_name, schedule_time, boss_time) VALUES (?, ?, ?, ?)',
            (POLL_GUILD_ID, '龍王', '08:30', None)
        )


def get_boss_settings():
    with db_connect() as conn:
        return conn.execute(
            'SELECT boss_name, schedule_time, boss_time FROM boss_poll_settings '
            'WHERE guild_id=? ORDER BY CASE WHEN schedule_time IS NULL THEN 1 ELSE 0 END, '
            'schedule_time, boss_name',
            (POLL_GUILD_ID,)
        ).fetchall()


def find_boss(name):
    with db_connect() as conn:
        return conn.execute(
            'SELECT boss_name, schedule_time, boss_time FROM boss_poll_settings '
            'WHERE guild_id=? AND boss_name=?',
            (POLL_GUILD_ID, name)
        ).fetchone()


def get_boss_post(name, date_text):
    with db_connect() as conn:
        return conn.execute(
            'SELECT channel_id, message_id, state FROM boss_poll_posts '
            'WHERE guild_id=? AND boss_name=? AND poll_date=?',
            (POLL_GUILD_ID, name, date_text)
        ).fetchone()


def boss_post_url(row):
    if row and row[1]:
        return f'https://discord.com/channels/{POLL_GUILD_ID}/{row[0]}/{row[1]}'
    return None


def normalize_schedule(value):
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = datetime.strptime(value, '%H:%M')
    except ValueError:
        raise ValueError('時間格式請輸入 24 小時制 HH:MM，例如 08:00 或 20:30')
    return parsed.strftime('%H:%M')


async def publish_boss_poll(boss_name, source='AUTO', boss_time_override=None):
    # 手動：每次執行都可產生新投票；自動：同一場排程當天只執行一次。
    # 共用鎖，避免自動輪詢並發或手動操作與自動排程相互干擾。
    async with poll_publish_lock:
        boss_setting = find_boss(boss_name)
        if boss_setting is None:
            return ('ERROR', f'找不到 BOSS「{boss_name}」，請先使用 /新增boss。')

        now = datetime.now(TAIWAN_TZ)
        poll_date = now.date().isoformat()
        schedule = boss_setting[1]  # 發布時間，只控制排程
        configured_boss_time = boss_setting[2]  # 打王時間，只顯示在投票上
        battle_time = boss_time_override if boss_time_override is not None else configured_boss_time
        is_auto = source == 'AUTO'
        if is_auto and not schedule:
            return ('SKIPPED', '此 BOSS 未設定自動發布時間。')

        # 選擇頻道並確認該頻道可用。
        channel = bot.get_channel(POLL_CHANNEL_ID)
        if channel is None:
            try:
                channel = await bot.fetch_channel(POLL_CHANNEL_ID)
            except (discord.HTTPException, discord.Forbidden) as exc:
                return ('ERROR', f'無法取得投票頻道：{exc}')
        if not isinstance(channel, discord.TextChannel):
            return ('ERROR', '投票頻道不是文字頻道')
        if channel.guild.id != POLL_GUILD_ID:
            return ('ERROR', '投票頻道不屬於設定的公會')

        # 「發布時間」與「打王時間」分開：標題只放打王時間。
        # 尚未設定打王時間時，不拿發布時間冒充。
        display_time = battle_time or '時間待定'
        publish_description = ('自動發布' if is_auto else '手動發布')

        try:
            poll = discord.Poll(
                question=(f'🐉 柏青哥｜{now:%m/%d} {boss_name} '
                          f'({display_time}) 打王職業投票'),
                duration=timedelta(hours=24),
                multiple=False
            )
            for job in POLL_JOBS:
                poll.add_answer(text=job)
        except Exception as exc:
            return ('ERROR', f'建立 Discord 投票失敗：{exc!r}')

        if is_auto:
            # 自動發布的唯一鍵：公會＋BOSS＋日期＋排程時間。
            # 手動不寫入這張表，因此手動發布不會擋掉 08:00 / 08:30 自動發布。
            with db_connect() as conn:
                cursor = conn.execute(
                    'INSERT OR IGNORE INTO boss_poll_auto_runs '
                    '(guild_id, boss_name, poll_date, schedule_time, state, message_id, created_at) '
                    'VALUES (?, ?, ?, ?, ?, NULL, ?)',
                    (POLL_GUILD_ID, boss_name, poll_date, schedule, 'PENDING', now.isoformat())
                )
                if cursor.rowcount == 0:
                    return ('SKIPPED', f'{boss_name} {poll_date} {schedule} 自動投票已處理，不重複發布。')

        try:
            message = await channel.send(
                content=(f'@everyone\n🐉 **{boss_name}** 今日打王職業投票已開放！'
                         f'\n📅 打王時間：**{display_time}'
                         + ('（台灣時間）**' if battle_time else '**')
                         + f'\n📢 投票發布：{publish_description}｜{now:%H:%M}（台灣時間）'
                         + '\n請每位夥伴選擇一個職業。'),
                poll=poll,
                allowed_mentions=discord.AllowedMentions(
                    everyone=True, users=False, roles=False
                )
            )
        except Exception as exc:
            if is_auto:
                # 發送明確失敗時清除占位，讓下次輪詢可以重試。
                with db_connect() as conn:
                    conn.execute(
                        'DELETE FROM boss_poll_auto_runs '
                        'WHERE guild_id=? AND boss_name=? AND poll_date=? '
                        'AND schedule_time=? AND state=?',
                        (POLL_GUILD_ID, boss_name, poll_date, schedule, 'PENDING')
                    )
            return ('ERROR', f'發布失敗：{exc!r}')

        try:
            with db_connect() as conn:
                conn.execute(
                    'INSERT INTO boss_poll_history '
                    '(guild_id, boss_name, poll_date, channel_id, message_id, source, created_at) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (POLL_GUILD_ID, boss_name, poll_date, POLL_CHANNEL_ID,
                     message.id, source, now.isoformat())
                )
                if is_auto:
                    conn.execute(
                        'UPDATE boss_poll_auto_runs SET state=?, message_id=? '
                        'WHERE guild_id=? AND boss_name=? AND poll_date=? AND schedule_time=?',
                        ('SENT', message.id, POLL_GUILD_ID, boss_name, poll_date, schedule)
                    )
        except sqlite3.Error as exc:
            # 訊息已實際送達，不能當成失敗重新發一次。
            print(f'投票已送達，但保存資料庫紀錄失敗：{exc!r}')

        print(f'{source}：{boss_name} {poll_date} 投票已發布 {message.jump_url}')
        return ('SENT', message.jump_url)


async def boss_autocomplete(interaction: discord.Interaction, current: str):
    names = [row[0] for row in get_boss_settings()]
    return [
        discord.app_commands.Choice(name=name, value=name)
        for name in names if current.lower() in name.lower()
    ][:25]


def boss_command_allowed(interaction):
    return interaction.guild is not None and (
        interaction.guild.id == POLL_GUILD_ID and is_manager(interaction)
    )


@bot.tree.command(
    name='每日投票',
    description='選擇一個 BOSS，立即發布新的職業投票（可重複發布）',
    guild=discord.Object(id=POLL_GUILD_ID)
)
@discord.app_commands.describe(boss='選擇今天要發布的 BOSS', 打王時間='本次打王時間，可覆蓋預設，例如 21:00（可留空）')
@discord.app_commands.autocomplete(boss=boss_autocomplete)
async def manual_daily_poll(interaction: discord.Interaction, boss: str, 打王時間: str = None):
    if not boss_command_allowed(interaction):
        await interaction.response.send_message('只有公會管理員可以發布投票。', ephemeral=True)
        return
    try:
        battle_override = normalize_schedule(打王時間)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    # 手動指令只負責發布正式投票，不額外留下「已發布」私人訊息。
    # Discord 必須先回應斜線指令，所以先暫時 defer，處理完即刪除。
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        status, info = await publish_boss_poll(boss, 'MANUAL', boss_time_override=battle_override)
    except Exception as exc:
        status, info = 'ERROR', f'{type(exc).__name__}: {exc}'
        print(f'手動發布 {boss} 投票失敗：{info}')

    if status == 'SENT':
        # 已發布到指定頻道；不額外留下成功提示。
        try:
            await interaction.delete_original_response()
        except discord.HTTPException as exc:
            print(f'清除手動發布提示失敗：{exc!r}')
    else:
        await interaction.edit_original_response(content=f'❌ {info}')


@bot.tree.command(
    name='新增boss',
    description='新增 BOSS，可分別指定投票發布時間和打王時間',
    guild=discord.Object(id=POLL_GUILD_ID)
)
@discord.app_commands.describe(名稱='例如：炎魔', 發布時間='每天幾點自動發投票，例如 08:00；不填則只手動發布', 打王時間='實際打王時間，例如 20:00')
async def add_boss(interaction: discord.Interaction, 名稱: str, 發布時間: str = None, 打王時間: str = None):
    if not boss_command_allowed(interaction):
        await interaction.response.send_message('只有公會管理員可新增 BOSS。', ephemeral=True)
        return
    name = 名稱.strip()
    if not name or len(name) > 40 or '@' in name or '\n' in name:
        await interaction.response.send_message('BOSS 名稱請使用 1～40 字，且不能含 @。', ephemeral=True)
        return
    try:
        schedule = normalize_schedule(發布時間)
        battle_time = normalize_schedule(打王時間)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    with db_connect() as conn:
        try:
            conn.execute('INSERT INTO boss_poll_settings '
                         '(guild_id, boss_name, schedule_time, boss_time) VALUES (?, ?, ?, ?)',
                         (POLL_GUILD_ID, name, schedule, battle_time))
        except sqlite3.IntegrityError:
            await interaction.response.send_message('這個 BOSS 已存在，可用 /設定boss時間 修改。', ephemeral=True)
            return
    await interaction.response.send_message(
        f'✅ 已新增「{name}」；發布：' + (f'每天 {schedule}' if schedule else '僅手動')
        + f'；打王時間：{battle_time or "未設定"}。',
        ephemeral=True
    )


@bot.tree.command(
    name='設定boss時間',
    description='設定 BOSS 每日投票發布時間（不會改變打王時間）',
    guild=discord.Object(id=POLL_GUILD_ID)
)
@discord.app_commands.describe(boss='要修改的 BOSS', 時間='例如 08:00、20:30，或輸入 關閉')
@discord.app_commands.autocomplete(boss=boss_autocomplete)
async def set_boss_time(interaction: discord.Interaction, boss: str, 時間: str):
    if not boss_command_allowed(interaction):
        await interaction.response.send_message('只有公會管理員可以修改時間。', ephemeral=True)
        return
    if not find_boss(boss):
        await interaction.response.send_message('找不到這個 BOSS。', ephemeral=True)
        return
    try:
        schedule = None if 時間.strip() == '關閉' else normalize_schedule(時間)
        if 時間.strip() != '關閉' and schedule is None:
            raise ValueError('請輸入 HH:MM 或「關閉」。')
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    with db_connect() as conn:
        conn.execute('UPDATE boss_poll_settings SET schedule_time=? '
                     'WHERE guild_id=? AND boss_name=?',
                     (schedule, POLL_GUILD_ID, boss))
    await interaction.response.send_message(
        f'✅ {boss} 已設定為' + (f'每天 {schedule} 自動發布。' if schedule else '只允許手動發布。'),
        ephemeral=True
    )


@bot.tree.command(
    name='設定boss打王時間',
    description='獨立設定 BOSS 打王時間，不影響投票發布時間',
    guild=discord.Object(id=POLL_GUILD_ID)
)
@discord.app_commands.describe(boss='要修改的 BOSS', 時間='實際打王時間，例如 21:00；輸入 關閉 可顯示時間待定')
@discord.app_commands.autocomplete(boss=boss_autocomplete)
async def set_boss_battle_time(interaction: discord.Interaction, boss: str, 時間: str):
    if not boss_command_allowed(interaction):
        await interaction.response.send_message('只有公會管理員可以修改時間。', ephemeral=True)
        return
    if not find_boss(boss):
        await interaction.response.send_message('找不到這個 BOSS。', ephemeral=True)
        return
    try:
        battle_time = None if 時間.strip() == '關閉' else normalize_schedule(時間)
        if 時間.strip() != '關閉' and battle_time is None:
            raise ValueError('請輸入 HH:MM 或「關閉」。')
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    with db_connect() as conn:
        conn.execute('UPDATE boss_poll_settings SET boss_time=? '
                     'WHERE guild_id=? AND boss_name=?',
                     (battle_time, POLL_GUILD_ID, boss))
    await interaction.response.send_message(
        f'✅ {boss} 打王時間：{battle_time or "時間待定"}（發布排程不變）。',
        ephemeral=True
    )


@bot.tree.command(
    name='boss清單',
    description='查看 BOSS 的投票發布時間與實際打王時間',
    guild=discord.Object(id=POLL_GUILD_ID)
)
async def list_boss(interaction: discord.Interaction):
    if not boss_command_allowed(interaction):
        await interaction.response.send_message('只有公會管理員可以查詢。', ephemeral=True)
        return
    rows = get_boss_settings()
    lines = [f'• {name}：發布 {schedule or "僅手動"}｜打王 {battle or "時間待定"}'
             for name, schedule, battle in rows]
    await interaction.response.send_message(
        '🐉 **每日職業投票 BOSS 清單**\n' + ('\n'.join(lines) or '目前無 BOSS'),
        ephemeral=True
    )


@bot.tree.command(
    name='刪除boss',
    description='停用並刪除 BOSS 的未來投票排程，不刪除已發布的投票',
    guild=discord.Object(id=POLL_GUILD_ID)
)
@discord.app_commands.autocomplete(boss=boss_autocomplete)
async def delete_boss(interaction: discord.Interaction, boss: str):
    if not boss_command_allowed(interaction):
        await interaction.response.send_message('只有公會管理員可以刪除。', ephemeral=True)
        return
    with db_connect() as conn:
        cursor = conn.execute('DELETE FROM boss_poll_settings WHERE guild_id=? AND boss_name=?',
                              (POLL_GUILD_ID, boss))
        changed = cursor.rowcount > 0
    await interaction.response.send_message(
        (f'✅ 已停用「{boss}」；舊投票記錄仍保留。' if changed else '找不到這個 BOSS。'),
        ephemeral=True
    )


@tasks.loop(seconds=30)
async def auto_boss_polls():
    # 每 30 秒檢查；自動發布依資料庫排程紀錄防止同分鐘重複送出。
    now = datetime.now(TAIWAN_TZ)
    current_time = now.strftime('%H:%M')
    for boss_name, schedule, battle_time in get_boss_settings():
        if schedule != current_time:
            continue
        status, detail = await publish_boss_poll(boss_name, 'AUTO')
        if status == 'ERROR':
            print(f'自動投票 {boss_name} 失敗：{detail}')


@auto_boss_polls.before_loop
async def before_auto_boss_polls():
    await bot.wait_until_ready()


views_restored = False


@bot.event
async def on_ready():

    global views_restored

    try:

        if not auto_boss_polls.is_running():
            auto_boss_polls.start()
            print('BOSS 自動投票已啟用；依資料庫發布時間執行（台灣時間）')

        # 每日投票使用指定伺服器指令，避免等待全域指令傳播。
        guild_synced = await bot.tree.sync(guild=discord.Object(id=POLL_GUILD_ID))
        print('伺服器專用指令同步成功：', [cmd.name for cmd in guild_synced])

        # 原有指令維持全域，並清理舊版全域每日投票指令。
        synced = await bot.tree.sync()

        if not views_restored:

            open_records = (
                get_open_records()
            )

            for (
                record_id,
                message_id
            ) in open_records:

                try:

                    bot.add_view(
                        RecordView(
                            record_id
                        ),
                        message_id=message_id
                    )

                except Exception as e:

                    print(
                        '恢復按鈕失敗：',
                        record_id,
                        repr(e)
                    )

            views_restored = True

            print('已還原舊分贓卡按鈕；不強制更新無法存取的舊訊息。')

        print(
            '================================'
        )

        print(
            f'Bot 已登入：'
            f'{bot.user}'
        )

        print(
            f'Slash 指令同步完成：'
            f'{len(synced)} 個'
        )

        print(
            f'管理身分組：'
            f'{MANAGER_ROLE_NAME}'
        )

        for guild in bot.guilds:

            print(
                f'伺服器：'
                f'{guild.name}'
            )

            print(
                f'目前快取成員：'
                f'{len(guild.members)} 人'
            )

            print(
                f'Discord 顯示成員數：'
                f'{guild.member_count}'
            )

            for voice_channel in (
                guild.voice_channels
            ):

                print(
                    f'語音頻道：'
                    f'{voice_channel.name}'
                    f'｜目前 '
                    f'{len(voice_channel.members)} 人'
                )

        print(
            '新增寶物：'
            '最多2筆，名稱與金額分開'
        )

        print(
            '修改市價：'
            '處理過的寶物會重新計算'
        )

        print(
            '修改市價：'
            '原領取紀錄會全部重置'
        )

        print(
            '寶物狀態：'
            '🟨 = 尚有人未領'
        )

        print(
            '寶物狀態：'
            '✅ = 全部領完'
        )

        print(
            '================================'
        )

    except Exception as e:

        print(
            '啟動錯誤：',
            repr(e)
        )


if not TOKEN:
    raise RuntimeError('尚未設定 DISCORD_TOKEN；請到 Render 的 Environment 設定。')

upstash_sqlite.set_database_path(DB_FILE)
upstash_sqlite.restore_at_boot(DB_FILE)
init_db()
init_boss_poll_tables()
start_render_health_server()

bot.run(TOKEN)