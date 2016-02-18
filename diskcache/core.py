"Core disk and file-based cache API."

import errno
import functools as ft
import io
import os
import os.path as op
import sqlite3
import sys
import time
import uuid
import warnings

if sys.hexversion < 0x03000000:
    import cPickle as pickle
    TextType = unicode
    BytesType = str
    INT_TYPES = int, long
else:
    import pickle
    TextType = str
    BytesType = bytes
    INT_TYPES = int,

ENOVAL = object()

MIN_INT = -sys.maxsize - 1
MAX_INT = sys.maxsize

DATABASE_NAME = 'cache.sqlite3'
TIMEOUT = 60.0

MODE_NONE = 0
MODE_RAW = 1
MODE_BINARY = 2
MODE_TEXT = 3
MODE_PICKLE = 4

DEFAULT_SETTINGS = {
    u'statistics': 0, # False
    u'eviction_policy': u'least-recently-stored',
    u'size_limit': 2 ** 30, # 1gb
    u'cull_limit': 10,
    u'large_value_threshold': 2 ** 10, # 1kb, min 8
    u'sqlite_synchronous': u'NORMAL',
    u'sqlite_journal_mode': u'WAL',
    u'sqlite_cache_size': 2 ** 13, # 8,192 pages
    u'sqlite_mmap_size': 2 ** 27,  # 128mb

    # Metadata

    u'count': 0,
    u'size': 0,
    u'hits': 0,
    u'misses': 0,
}

EVICTION_POLICY = {
    'least-recently-stored': {
        'init': (
            'CREATE INDEX IF NOT EXISTS Cache_store_time ON'
            ' Cache (store_time)'
        ),
        'get': None,
        'set': (
            'SELECT rowid, version, filename FROM Cache'
            ' ORDER BY store_time LIMIT ?'
        ),
    },
    'least-recently-used': {
        'init': (
            'CREATE INDEX IF NOT EXISTS Cache_access_time ON'
            ' Cache (access_time)'
        ),
        'get': (
            'UPDATE Cache SET'
            ' access_time = ((julianday("now") - 2440587.5) * 86400.0)'
            ' WHERE rowid = ?'
        ),
        'set': (
            'SELECT rowid, version, filename FROM Cache'
            ' ORDER BY access_time LIMIT ?'
        ),
    },
    'least-frequently-used': {
        'init': (
            'CREATE INDEX IF NOT EXISTS Cache_access_count ON'
            ' Cache (access_count)'
        ),
        'get': (
            'UPDATE Cache SET'
            ' access_count = access_count + 1'
            ' WHERE rowid = ?'
        ),
        'set': (
            'SELECT rowid, version, filename FROM Cache'
            ' ORDER BY access_count LIMIT ?'
        ),
    },
}


class Disk(object):
    "Cache key and value serialization for disk and file."
    def __init__(self, pickle_protocol=pickle.HIGHEST_PROTOCOL):
        self._protocol = pickle_protocol


    def put(self, key):
        "Convert key to fields (key, raw) for Cache table."
        # pylint: disable=bad-continuation,unidiomatic-typecheck
        type_key = type(key)

        if type_key is BytesType:
            return sqlite3.Binary(key), True
        elif ((type_key is TextType)
                or (type_key in INT_TYPES and MIN_INT <= key <= MAX_INT)
                or (type_key is float)):
            return key, True
        else:
            result = pickle.dumps(key, protocol=self._protocol)
            return sqlite3.Binary(result), False


    def get(self, key, raw):
        "Convert fields (key, raw) from Cache table to key."
        # pylint: disable=no-self-use,unidiomatic-typecheck
        if raw:
            return BytesType(key) if type(key) is sqlite3.Binary else key
        else:
            return pickle.load(io.BytesIO(key))


    def store(self, value, read, threshold, prep_file):
        """Return fields (size, mode, filename, value) for Cache table.

        Arguments:
        value -- value to convert
        read -- True iff value is file-like object
        threshold -- size threshold for large values
        prep_file -- callable returning (filename, full_path) pair

        """
        # pylint: disable=unidiomatic-typecheck
        type_value = type(value)

        if ((type_value is TextType and len(value) < threshold)
                or (type_value in INT_TYPES and MIN_INT <= value <= MAX_INT)
                or (type_value is float)):
            return 0, MODE_RAW, None, value
        elif type_value is BytesType:
            if len(value) < threshold:
                return len(value), MODE_RAW, None, sqlite3.Binary(value)
            else:
                filename, full_path = prep_file()

                with io.open(full_path, 'wb') as writer:
                    writer.write(value)

                return len(value), MODE_BINARY, filename, None
        elif type_value is TextType:
            filename, full_path = prep_file()

            with io.open(full_path, 'w', encoding='UTF-8') as writer:
                writer.write(value)

            size = op.getsize(full_path)

            return size, MODE_TEXT, filename, None
        elif read:
            size = 0
            reader = ft.partial(value.read, 2 ** 22)
            filename, full_path = prep_file()

            with io.open(full_path, 'wb') as writer:
                for chunk in iter(reader, b''):
                    size += len(chunk)
                    writer.write(chunk)

            return size, MODE_BINARY, filename, None
        else:
            result = pickle.dumps(value, protocol=self._protocol)

            if len(result) < threshold:
                return 0, MODE_PICKLE, None, sqlite3.Binary(result)
            else:
                filename, full_path = prep_file()

                with io.open(full_path, 'wb') as writer:
                    writer.write(result)

                return len(result), MODE_PICKLE, filename, None


    def fetch(self, directory, mode, filename, value, read):
        "Convert fields (mode, filename, value) from Cache table to value."
        # pylint: disable=no-self-use,unidiomatic-typecheck
        if mode == MODE_RAW:
            return BytesType(value) if type(value) is sqlite3.Binary else value
        elif mode == MODE_BINARY:
            if read:
                return io.open(op.join(directory, filename), 'rb')
            else:
                with io.open(op.join(directory, filename), 'rb') as reader:
                    return reader.read()
        elif mode == MODE_TEXT:
            with io.open(op.join(directory, filename), 'rt') as reader:
                return reader.read()
        elif mode == MODE_PICKLE:
            if value is None:
                with io.open(op.join(directory, filename), 'rb') as reader:
                    return pickle.load(reader)
            else:
                return pickle.load(io.BytesIO(value))


class CachedAttr(object):
    "Data descriptor that caches get's and writes set's back to the database."
    # pylint: disable=too-few-public-methods
    def __init__(self, key):
        self._key = key
        self._value = '_' + key
        self._pragma = key.startswith('sqlite_') and key[7:]

    def __get__(self, cache, cache_type):
        return getattr(cache, self._value)

    def __set__(self, cache, value):
        "Cache attribute value and write back to database."
        # pylint: disable=protected-access,attribute-defined-outside-init
        sql = cache._sql.execute
        query = 'INSERT OR REPLACE INTO Settings VALUES (?, ?)'
        sql(query, (self._key, value))

        if self._pragma:

            # 2016-02-17 GrantJ - PRAGMA and autocommit_level=None don't always
            # play nicely together. Retry setting the PRAGMA. I think some
            # PRAGMA statements expect to immediately take an EXCLUSIVE lock on
            # the database. I can't find any documentation for this but without
            # the retry, stress will intermittently fail with multiple
            # processes.

            error = sqlite3.OperationalError

            for _ in range(int(TIMEOUT / 0.001)): # Wait up to ~60 seconds.
                try:
                    sql('PRAGMA %s = %s' % (self._pragma, value)).fetchone()
                except sqlite3.OperationalError as exc:
                    error = exc
                    time.sleep(0.001)
                else:
                    break
            else:
                raise error

            del error

        setattr(cache, self._value, value)

    def __delete__(self, cache):
        "Update descriptor value from database."
        # pylint: disable=protected-access,attribute-defined-outside-init
        query = 'SELECT value FROM Settings WHERE key = ?'
        value, = cache._sql.execute(query, (self._key,)).fetchone()
        setattr(cache, self._value, value)


class CacheMeta(type):
    "Metaclass for Cache to make Settings into attributes."
    def __new__(mcs, name, bases, attrs):
        for key in DEFAULT_SETTINGS:
            attrs[key] = CachedAttr(key)
        return type.__new__(mcs, name, bases, attrs)


# Copied from bitbucket.org/gutworth/six/six.py Seems excessive to depend on
# `six` when only this snippet is needed. Metaclass syntax changed in Python 3.

def with_metaclass(meta, *bases):
    """Create a base class with a metaclass."""
    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class DummyMetaclass(meta):
        "Dummy metaclass for Python 2 and Python 3 compatibility."
        # pylint: disable=too-few-public-methods
        def __new__(cls, name, _, attrs):
            return meta(name, bases, attrs)
    return type.__new__(DummyMetaclass, 'temporary_class', (), {})


class EmptyDirWarning(UserWarning):
    "Warning used by Cache.check for empty directories."
    pass


class Cache(with_metaclass(CacheMeta, object)):
    "Disk and file-based cache."
    # pylint: disable=bad-continuation
    def __init__(self, directory, disk=Disk(), **settings):
        self._dir = directory
        self._disk = disk

        if not op.isdir(directory):
            try:
                os.makedirs(directory, 0o700)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise EnvironmentError(
                        error.errno,
                        'Cache directory "%s" does not exist'
                        ' and could not be created' % self._dir
                    )

        _sql = self._sql = sqlite3.connect(
            op.join(directory, DATABASE_NAME),
            timeout=TIMEOUT,
            isolation_level=None,
        )
        sql = _sql.execute

        # Setup Settings table.

        sql('CREATE TABLE IF NOT EXISTS Settings ('
            ' key TEXT NOT NULL UNIQUE,'
            ' value)'
        )

        current_settings = dict(sql(
            'SELECT key, value FROM Settings'
        ).fetchall())

        temp = DEFAULT_SETTINGS.copy()
        temp.update(current_settings)
        temp.update(settings)

        # Set cached attributes: updates settings and sets pragmas.

        for key, value in temp.items():
            setattr(self, key, value)

        self._page_size, = sql('PRAGMA page_size').fetchone()

        # Setup Cache table.

        sql('CREATE TABLE IF NOT EXISTS Cache ('
            ' rowid INTEGER PRIMARY KEY,'
            ' key BLOB,'
            ' raw INTEGER,'
            ' version INTEGER DEFAULT 0,'
            ' store_time REAL,'
            ' expire_time REAL,'
            ' access_time REAL,'
            ' access_count INTEGER DEFAULT 0,'
            ' tag BLOB,'
            ' size INTEGER DEFAULT 0,'
            ' mode INTEGER DEFAULT 0,'
            ' filename TEXT,'
            ' value BLOB)'
        )

        sql('CREATE UNIQUE INDEX IF NOT EXISTS Cache_key_raw ON'
            ' Cache(key, raw)'
        )

        sql('CREATE INDEX IF NOT EXISTS Cache_expire_time ON'
            ' Cache (expire_time)'
        )

        query = EVICTION_POLICY[self.eviction_policy]['init']

        if query is not None:
            sql(query)

        # Use triggers to keep Metadata updated.

        sql('CREATE TRIGGER IF NOT EXISTS Settings_count_insert'
            ' AFTER INSERT ON Cache FOR EACH ROW BEGIN'
            ' UPDATE Settings SET value = value + 1'
            ' WHERE key = "count"; END'
        )

        sql('CREATE TRIGGER IF NOT EXISTS Settings_count_delete'
            ' AFTER DELETE ON Cache FOR EACH ROW BEGIN'
            ' UPDATE Settings SET value = value - 1'
            ' WHERE key = "count"; END'
        )

        sql('CREATE TRIGGER IF NOT EXISTS Settings_size_insert'
            ' AFTER INSERT ON Cache FOR EACH ROW BEGIN'
            ' UPDATE Settings SET value = value + NEW.size'
            ' WHERE key = "size"; END'
        )

        sql('CREATE TRIGGER IF NOT EXISTS Settings_size_update'
            ' AFTER UPDATE ON Cache FOR EACH ROW BEGIN'
            ' UPDATE Settings'
            ' SET value = value + NEW.size - OLD.size'
            ' WHERE key = "size"; END'
        )

        sql('CREATE TRIGGER IF NOT EXISTS Settings_size_delete'
            ' AFTER DELETE ON Cache FOR EACH ROW BEGIN'
            ' UPDATE Settings SET value = value - OLD.size'
            ' WHERE key = "size"; END'
        )


    def set(self, key, value, read=False, expire=None, tag=None):
        """Store key, value pair in cache.

        When `read` is `True`, `value` should be a file-like object opened
        for reading in binary mode.

        Keyword arguments:
        expire -- seconds until the key expires (default None, no expiry)
        tag -- text to associate with key (default None)
        read -- read value as raw bytes from file (default False)
        """
        sql = self._sql.execute

        db_key, raw = self._disk.put(key)

        # Lookup filename for existing key.

        row = sql(
            'SELECT version, filename FROM Cache WHERE key = ? AND raw = ?',
            (db_key, raw)
        ).fetchone()

        if row:
            version, filename = row
        else:
            sql('INSERT OR IGNORE INTO Cache(key, raw) VALUES (?, ?)',
                (db_key, raw)
            )
            version, filename = 0, None

        # Remove existing file if present.

        if filename is not None:
            self._remove(filename)

        # Prepare value for disk storage.

        size, mode, filename, db_value = self._disk.store(
            value, read, self.large_value_threshold, self._prep_file
        )

        next_version = version + 1
        now = time.time()
        expire_time = None if expire is None else now + expire

        # Update the row. Two step process so that all files remain tracked.

        cursor = sql(
            'UPDATE Cache SET'
            ' version = ?,'
            ' store_time = ?,'
            ' expire_time = ?,'
            ' access_time = ?,'
            ' access_count = ?,'
            ' tag = ?,'
            ' size = ?,'
            ' mode = ?,'
            ' filename = ?,'
            ' value = ?'
            ' WHERE key = ? AND raw = ? AND version = ?', (
                next_version,
                now,          # store_time
                expire_time,
                now,          # access_time
                0,            # access_count
                tag,
                size,
                mode,
                filename,
                db_value,
                db_key,
                raw,
                version,
            ),
        )

        if cursor.rowcount == 0:
            # Another Cache wrote the value before us so abort.
            if filename is not None:
                self._remove(filename)
            return

        # Evict expired keys.

        cull_limit = self.cull_limit

        rows = sql(
            'SELECT rowid, version, filename FROM Cache'
            ' WHERE expire_time IS NOT NULL AND expire_time < ?'
            ' ORDER BY expire_time LIMIT ?',
            (now, cull_limit),
        ).fetchall()

        for rowid, version, filename in rows:
            deleted = self._delete(rowid, version, filename)
            if deleted:
                cull_limit -= 1

        if cull_limit == 0:
            return

        # Calculate total size.

        page_count, = sql('PRAGMA page_count').fetchone()
        del self.size # Update value from database.
        total_size = self._page_size * page_count + self.size

        if total_size < self.size_limit:
            return

        # Evict keys by policy.

        query = EVICTION_POLICY[self.eviction_policy]['set']

        if query is not None:
            rows = sql(query, (cull_limit,))

            for rowid, version, filename in rows:
                self._delete(rowid, version, filename)

    __setitem__ = set


    def get(self, key, default=None, read=False, expire_time=False, tag=False):
        """Get key from cache. If key is missing, return default.

        Keyword arguments:
        default -- value to return if key is missing (default None)
        read -- if True, return open file handle to value (default False)
        expire_time -- if True, return expire_time in tuple (default False)
        tag -- if True, return tag in tuple (default False)
        """
        sql = self._sql.execute
        cache_hit = 'UPDATE Settings SET value = value + 1 WHERE key = "hits"'
        cache_miss = (
            'UPDATE Settings SET value = value + 1'
            ' WHERE key = "misses"'
        )

        if expire_time and tag:
            default = (default, None, None)
        elif expire_time or tag:
            default = (default, None)

        db_key, raw = self._disk.put(key)

        row = sql(
            'SELECT rowid, store_time, expire_time, tag,'
            ' mode, filename, value'
            ' FROM Cache WHERE key = ? AND raw = ?',
            (db_key, raw),
        ).fetchone()

        if row is None:
            if self.statistics:
                sql(cache_miss)
            return default

        (rowid, store_time, db_expire_time, db_tag,
            mode, filename, db_value) = row

        if store_time is None:
            if self.statistics:
                sql(cache_miss)
            return default

        now = time.time()

        if db_expire_time is not None and db_expire_time < now:
            if self.statistics:
                sql(cache_miss)
            return default

        try:
            value = self._disk.fetch(self._dir, mode, filename, db_value, read)
        except IOError as error:
            if error.errno == errno.ENOENT:
                # Key was deleted before we could retrieve result.
                if self.statistics:
                    sql(cache_miss)
                return default
            else:
                raise

        if self.statistics:
            sql(cache_hit)

        query = EVICTION_POLICY[self.eviction_policy]['get']

        if query is not None:
            sql(query, (rowid,))

        if expire_time and tag:
            return (value, db_expire_time, db_tag)
        elif expire_time:
            return (value, db_expire_time)
        elif tag:
            return (value, db_tag)
        else:
            return value


    def __getitem__(self, key):
        value = self.get(key, default=ENOVAL)
        if value is ENOVAL:
            raise KeyError(key)
        return value


    def __delitem__(self, key):
        sql = self._sql.execute

        db_key, raw = self._disk.put(key)

        row = sql(
            'SELECT rowid, version, filename'
            ' FROM Cache WHERE key = ? AND raw = ?',
            (db_key, raw),
        ).fetchone()

        if row is None:
            raise KeyError(key)
        else:
            self._delete(*row)


    def delete(self, key):
        "Delete key from cache. Missing keys are ignored."
        try:
            del self[key]
        except KeyError:
            pass


    def _delete(self, rowid, version, filename):
        cursor = self._sql.execute(
            'DELETE FROM Cache WHERE rowid = ? AND version = ?',
            (rowid, version),
        )

        deleted = cursor.rowcount == 1

        if deleted and filename is not None:
            self._remove(filename)

        return deleted


    def _prep_file(self):
        hex_name = uuid.uuid4().hex
        sub_dir = op.join(hex_name[:2], hex_name[2:4])
        name = hex_name[4:] + '.val'
        directory = op.join(self._dir, sub_dir)

        try:
            os.makedirs(directory)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise

        filename = op.join(sub_dir, name)
        full_path = op.join(self._dir, filename)

        return filename, full_path


    def _remove(self, filename):
        full_path = op.join(self._dir, filename)

        try:
            os.remove(full_path)
        except OSError as error:
            if error.errno != errno.ENOENT:
                # ENOENT may occur if two caches attempt to delete the same
                # file at the same time.
                raise


    def check(self, fix=False):
        "Check database and file system consistency."
        # pylint: disable=access-member-before-definition,W0201
        sql = self._sql.execute

        # Check integrity of database.

        rows = sql('PRAGMA integrity_check').fetchall()

        if len(rows) != 1 or rows[0][0] != u'ok':
            for message, in rows:
                warnings.warn(message)

        if fix:
            sql('VACUUM')

        # Check Settings.count against count of Cache rows.

        del self.count
        self_count = self.count
        count, = sql('SELECT COUNT(key) FROM Cache').fetchone()

        if self_count != count:
            message = 'Settings.count != COUNT(Cache.key); %d != %d'
            warnings.warn(message % (self_count, count))

            if fix:
                self.count = count

        # Report uncommitted rows.

        rows = sql(
            'SELECT rowid, key, raw, version, filename FROM Cache'
            ' WHERE store_time IS NULL'
        ).fetchall()

        for rowid, key, raw, version, filename in rows:
            warnings.warn('row %d partially commited with key %r' % (
                rowid, self._disk.get(key, raw)
            ))
            if fix:
                self._delete(rowid, version, filename)

        # Check Cache.filename against file system.

        filenames = set()
        chunk = self.cull_limit
        rowid = 0
        total_size = 0

        while True:
            rows = sql(
                'SELECT rowid, version, filename FROM Cache'
                ' WHERE rowid > ? AND filename IS NOT NULL'
                ' ORDER BY rowid LIMIT ?',
                (rowid, chunk),
            ).fetchall()

            if not rows:
                break

            for rowid, version, filename in rows:
                full_path = op.join(self._dir, filename)
                filenames.add(full_path)

                if op.exists(full_path):
                    total_size += op.getsize(full_path)
                    continue

                warnings.warn('file not found: %s' % full_path)

                if fix:
                    self._delete(rowid, version, filename)

        del self.size
        self_size = self.size
        size, = sql('SELECT COALESCE(SUM(size), 0) FROM Cache').fetchone()

        if self_size != size:
            message = 'Settings.size != SUM(Cache.size); %d != %d'
            warnings.warn(message % (self_size, size))

            if fix:
                self.size = size

        # Check file system against Cache.filename.

        for dirpath, _, files in os.walk(self._dir):
            paths = [op.join(dirpath, filename) for filename in files]
            error = set(paths) - filenames

            for full_path in error:
                if DATABASE_NAME in full_path:
                    continue

                warnings.warn('unreferenced file: %s' % full_path)

                if fix:
                    os.remove(full_path)

        # Check for empty directories.

        for dirpath, dirs, files in os.walk(self._dir):
            if not (dirs or files):
                warnings.warn('empty directory: %s' % dirpath, EmptyDirWarning)

                if fix:
                    os.rmdir(dirpath)


    def expire(self):
        "Remove expired items from Cache."

        now = time.time()
        sql = self._sql.execute
        chunk = self.cull_limit
        expire_time = 0

        while True:
            rows = sql(
                'SELECT rowid, version, expire_time, filename FROM Cache'
                ' WHERE ? < expire_time AND expire_time < ?'
                ' ORDER BY expire_time LIMIT ?',
                (expire_time, now, chunk),
            ).fetchall()

            if not rows:
                break

            for rowid, version, expire_time, filename in rows:
                self._delete(rowid, version, filename)


    def evict(self, tag):
        "Remove items with matching tag from Cache."

        sql = self._sql.execute
        chunk = self.cull_limit
        rowid = 0

        sql('CREATE INDEX IF NOT EXISTS Cache_tag_rowid ON'
            ' Cache(tag, rowid)'
        )

        while True:
            rows = sql(
                'SELECT rowid, version, filename FROM Cache'
                ' WHERE tag = ? AND rowid > ? ORDER BY rowid LIMIT ?',
                (tag, rowid, chunk),
            ).fetchall()

            if not rows:
                break

            for rowid, version, filename in rows:
                self._delete(rowid, version, filename)


    def clear(self):
        "Remove all items from Cache."

        sql = self._sql.execute
        chunk = self.cull_limit
        rowid = 0

        while True:
            rows = sql(
                'SELECT rowid, version, filename FROM Cache'
                ' WHERE rowid > ? ORDER BY rowid LIMIT ?',
                (rowid, chunk),
            ).fetchall()

            if not rows:
                break

            for rowid, version, filename in rows:
                self._delete(rowid, version, filename)


    def stats(self, enable=True, reset=False):
        """Return cache statistics pair: hits, misses.

        Keyword arguments:
        enable -- enable collecting statistics (default True)
        reset -- reset hits and misses to 0 (default False)

        """
        # pylint: disable=E0203,W0201
        del self.hits
        del self.misses

        result = (self.hits, self.misses)

        if reset:
            self.hits = 0
            self.misses = 0

        self.statistics = enable

        return result


    def close(self):
        "Close database connection."
        self._sql.close()


    def __len__(self):
        del self.count
        return self.count
