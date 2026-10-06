"""Minimal Turso/libSQL client over the Hrana HTTP pipeline, shaped like the sqlite3 calls Store uses.

Serverless hosts run several instances with no shared disk, so the SQLite file moves behind HTTP.
"""
import base64
import sqlite3

import httpx


class Row(dict):
    """Column-name access like sqlite3.Row, plus positional access."""
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, rows=(), rowcount=0):
        self.rows, self.rowcount = list(rows), rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def _encode(value):
    if value is None:
        return {'type': 'null'}
    if isinstance(value, bool) or isinstance(value, int):
        return {'type': 'integer', 'value': str(int(value))}
    if isinstance(value, float):
        return {'type': 'float', 'value': value}
    if isinstance(value, bytes):
        return {'type': 'blob', 'base64': base64.b64encode(value).decode()}
    return {'type': 'text', 'value': str(value)}


def _decode(cell):
    kind = cell['type']
    if kind == 'null':
        return None
    if kind == 'integer':
        return int(cell['value'])
    if kind == 'blob':
        return base64.b64decode(cell['base64'] + '=' * (-len(cell['base64']) % 4))
    return cell['value']


class Connection:
    """One Hrana stream. Like sqlite3, a transaction opens before the first write and ends on commit/rollback."""
    def __init__(self, url, token, transport=None):
        self.url = url.replace('libsql://', 'https://').rstrip('/') + '/v2/pipeline'
        self.http = httpx.Client(timeout=httpx.Timeout(15, connect=5), transport=transport,
                                 headers={'Authorization': f'Bearer {token}'})
        self.baton, self.in_transaction = None, False

    def _send(self, *requests):
        try:
            response = self.http.post(self.url, json={'baton': self.baton, 'requests': list(requests)})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise sqlite3.OperationalError(f'database request failed: {type(exc).__name__}') from None
        self.baton = body.get('baton')
        results = []
        for item in body['results']:
            if item['type'] == 'error':
                message = item['error'].get('message', '')
                # Callers distinguish duplicates (409) from storage failures (503) by exception class.
                error = sqlite3.IntegrityError if 'constraint' in message.lower() else sqlite3.OperationalError
                raise error(message)
            results.append(item['response'].get('result'))
        return results

    def execute(self, sql, parameters=()):
        head = sql.lstrip().split(None, 1)[0].upper()
        if head == 'BEGIN':
            self.in_transaction = True
        elif head in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'} and not self.in_transaction:
            self._send({'type': 'execute', 'stmt': {'sql': 'BEGIN'}})
            self.in_transaction = True
        result = self._send({'type': 'execute', 'stmt': {'sql': sql, 'args': [_encode(v) for v in parameters]}})[0]
        names = [column['name'] for column in result['cols']]
        return Result((Row(zip(names, map(_decode, row))) for row in result['rows']), result.get('affected_row_count', 0))

    def executemany(self, sql, rows):
        for parameters in rows:
            self.execute(sql, parameters)

    def executescript(self, script):
        self.commit()
        self._send({'type': 'sequence', 'sql': script})

    def _finish(self, statement):
        if self.in_transaction:
            self.in_transaction = False
            self._send({'type': 'execute', 'stmt': {'sql': statement}})

    def commit(self):
        self._finish('COMMIT')

    def rollback(self):
        try:
            self._finish('ROLLBACK')
        except sqlite3.Error:
            pass  # The server drops an abandoned stream and its transaction.

    def close(self):
        try:
            if self.baton:
                self._send({'type': 'close'})
        except sqlite3.Error:
            pass
        finally:
            self.http.close()
