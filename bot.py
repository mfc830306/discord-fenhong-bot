



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
TAIWAN_TZ = ZoneInfo('Asia/Taipei')


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
    return datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d %H:%M:%S')


def today_text():
    return datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d')


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
