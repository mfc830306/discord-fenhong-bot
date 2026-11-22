"""Single-process SQLite persistence to Upstash Redis using compressed snapshots.

This adapter is intended for ONE bot writer instance. It is not a distributed SQL
engine. Keep the original SQLite database backups independently.
"""
import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone

KEY = 'fenhong:sqlite:v1'
SYNC_LOCK = threading.RLock()
ACTIVE = False


def _credentials():
    url = (os.environ.get('UPSTASH_REDIS_REST_URL') or '').strip().rstrip('/')
    token = (os.environ.get('UPSTASH_REDIS_REST_TOKEN') or '').strip()
    if not url.startswith('https://') or not token:
        raise RuntimeError('缺少有效的 UPSTASH_REDIS_REST_URL / TOKEN，拒絕啟動以保護分贓資料')
    return url, token


def _command(*parts):
    url, token = _credentials()
    body = json.dumps(list(parts), ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError('Upstash Redis 無法讀寫，為保護資料停止操作') from exc
    if not isinstance(result, dict) or 'error' in result:
        raise RuntimeError('Upstash Redis 回傳錯誤：' + str(result.get('error') if isinstance(result, dict) else result))
    return result.get('result')


def _pack(raw):
    return json.dumps({
        'format': 'fenhong-sqlite-snapshot-v1',
        'sha256': hashlib.sha256(raw).hexdigest(),
        'saved_at': datetime.now(timezone.utc).isoformat(),
        'data': base64.b64encode(zlib.compress(raw, 6)).decode('ascii'),
    }, ensure_ascii=False, separators=(',', ':'))


def _unpack(payload):
    obj = json.loads(payload)
    if obj.get('format') != 'fenhong-sqlite-snapshot-v1':
        raise RuntimeError('Redis 快照格式不符')
    raw = zlib.decompress(base64.b64decode(obj['data'], validate=True))
    if hashlib.sha256(raw).hexdigest() != obj.get('sha256'):
        raise RuntimeError('Redis 快照 SHA256 檢查失敗')
    return raw


def _validated_memory_db(raw):
    conn = sqlite3.connect(':memory:')
    try:
        conn.deserialize(raw)
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite 完整性檢查未通過')
        return conn
    except Exception:
        conn.close()
        raise


def import_backup_file(filepath):
    """One-time upload; refuses to overwrite an existing Redis snapshot."""
    _credentials()
    if _command('EXISTS', KEY):
        raise RuntimeError('Upstash 已存在分贓資料；拒絕覆蓋！')
    uri = 'file:' + os.path.abspath(filepath).replace('\\', '/') + '?mode=ro'
    source = sqlite3.connect(uri, uri=True)
    memory = sqlite3.connect(':memory:')
    try:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('來源 SQLite 完整性檢查失敗')
        source.backup(memory)
        raw = memory.serialize()
        payload = _pack(raw)
        # NX guarantees we never overwrite prior migration or live bot data.
        if _command('SET', KEY, payload, 'NX') != 'OK':
            raise RuntimeError('寫入遭拒，Redis 可能已存在資料；未覆蓋')
        stored = _command('GET', KEY)
        if _unpack(stored) != raw:
            raise RuntimeError('寫入後讀回資料不一致；立即停止切換')
        return len(raw), len(payload.encode('utf-8'))
    finally:
        source.close()
        memory.close()


def restore_at_boot(path):
    """Fail closed if Redis empty. Never silently start a new blank database."""
    global ACTIVE
    with SYNC_LOCK:
        _credentials()
        payload = _command('GET', KEY)
        if not payload:
            raise RuntimeError('Upstash 尚無舊資料快照，請先匯入 SQLite；拒絕建立空資料庫')
        raw = _unpack(payload)
        memory = _validated_memory_db(raw)
        folder = os.path.dirname(os.path.abspath(path)) or '.'
        fd, temporary = tempfile.mkstemp(prefix='fenhong_restore_', suffix='.db', dir=folder)
        os.close(fd)
        try:
            disk = sqlite3.connect(temporary)
            try:
                memory.backup(disk)
            finally:
                disk.close()
            os.replace(temporary, path)
        finally:
            memory.close()
            if os.path.exists(temporary):
                os.unlink(temporary)
        ACTIVE = True
        print(f'[Upstash] 從 Redis 恢復資料庫成功：{len(raw):,} bytes', flush=True)


def _save_snapshot():
    if not ACTIVE:
        raise RuntimeError('Upstash 同步尚未初始化')
    with SYNC_LOCK:
        source = sqlite3.connect('file:' + os.path.abspath(_DB_FILE).replace('\\', '/') + '?mode=ro', uri=True)
        memory = sqlite3.connect(':memory:')
        try:
            source.backup(memory)
            raw = memory.serialize()
            if _command('SET', KEY, _pack(raw)) != 'OK':
                raise RuntimeError('Upstash 快照寫入未確認成功')
        finally:
            memory.close()
            source.close()


_DB_FILE = 'fenhong.db'


def set_database_path(path):
    global _DB_FILE
    _DB_FILE = path


class MirroredConnection(sqlite3.Connection):
    """Mirror successful SQLite commits to Redis. Only one bot writer permitted."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_changes = self.total_changes
        self._previous_schema = self.execute('PRAGMA schema_version').fetchone()[0]

    def _has_changes(self):
        return (self.total_changes != self._previous_changes or
                self.execute('PRAGMA schema_version').fetchone()[0] != self._previous_schema)

    def _mirror(self):
        if self._has_changes():
            try:
                _save_snapshot()
            except Exception as exc:
                print(f"[Upstash] 嚴重：同步資料失敗，停止機器人防止資料被覆蓋：{exc!r}", flush=True)
                # A committed local change that is not mirrored must NEVER be
                # followed by more writes that mask the failure.
                os._exit(79)
            self._previous_changes = self.total_changes
            self._previous_schema = self.execute('PRAGMA schema_version').fetchone()[0]

    def commit(self):
        super().commit()
        self._mirror()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            result = super().__exit__(exc_type, exc_value, traceback)
            if exc_type is None:
                self._mirror()
            return result
        finally:
            self.close()


def connect(path):
    return sqlite3.connect(path, factory=MirroredConnection)
