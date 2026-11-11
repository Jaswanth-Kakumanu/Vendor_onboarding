import hashlib
import io
import json
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from pypdf import PdfReader
from werkzeug.exceptions import HTTPException
from .engine import evaluate, STAGES


def now():
    return datetime.now(timezone.utc).isoformat()


def create_app(test_config=None):
    app = Flask(__name__, static_folder='static')
    app.config.update(MAX_CONTENT_LENGTH=6 * 1024 * 1024, DATA_DIR=os.environ.get('VENDORFLOW_DATA_DIR', 'data'), TOKEN=os.environ.get('VENDORFLOW_TOKEN', ''))
    if test_config:
        app.config.update(test_config)
    directory = Path(app.config['DATA_DIR'])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = directory / 'vendorflow.sqlite3'
    def db():
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT, status TEXT, payload TEXT, documents TEXT, events TEXT, result TEXT)')
    db_path.chmod(0o600)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='onboarding')
    app.extensions['executor'] = executor
    run_lock = threading.Lock()

    @app.before_request
    def guard():
        if request.path.startswith('/api/') and app.config['TOKEN']:
            import hmac
            if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + app.config['TOKEN']):
                return jsonify(error='An access token is required. Enter it using Access in the sidebar.'), 401
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            # Browser requests must originate from this host. JSON and bearer auth also prevent form-based CSRF.
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                return jsonify(error='Cross-origin requests are not accepted.'), 403

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(Exception)
    def errors(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        app.logger.error('Unhandled application error: %s', type(error).__name__)
        return jsonify(error='The request could not be completed. Please retry.'), 500

    def load(row):
        return {**dict(row), **{key: json.loads(row[key]) if row[key] else None for key in ['payload', 'documents', 'events', 'result']}}

    def public(run, detail=True):
        payload = run['payload']
        out = dict(id=run['id'], created_at=run['created_at'], status=run['status'], legal_name=payload.get('legal_name', ''), country=payload.get('country', ''), email=payload.get('email', ''))
        if detail:
            bank = payload.get('bank', {})
            out.update(events=run['events'], result=run['result'], documents=[dict(kind=key, filename=value['filename'], sha256=value['sha256']) for key, value in run['documents'].items()],
                       summary=dict(address=payload.get('address', ''), tax_id='••••' + str(payload.get('tax_id', ''))[-4:], account_holder=bank.get('account_holder', ''), payment_reference='••••' + str(bank.get('iban') or bank.get('account_number', ''))[-4:]))
        return out

    def process(run_id):
        # Single supported server worker. Serializes duplicate checks and approval commits.
        with run_lock:
            with db() as conn:
                row = conn.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
                if not row or row['status'] not in {'queued', 'running'}:
                    return
                run = load(row)
                conn.execute('UPDATE runs SET status=?, events=? WHERE id=?', ('running', '[]', run_id))
            events = []
            def emit(stage, findings):
                events.append(dict(stage=stage, label=dict(STAGES)[stage], at=now(), findings=findings))
                with db() as conn:
                    conn.execute('UPDATE runs SET events=? WHERE id=?', (json.dumps(events), run_id))
            try:
                with db() as conn:
                    prior = [load(row) for row in conn.execute("SELECT * FROM runs WHERE status='approved'")]
                result = evaluate(run['payload'], run['documents'], prior, emit)
                with db() as conn:
                    conn.execute('UPDATE runs SET status=?,result=? WHERE id=?', (result['status'], json.dumps(result), run_id))
            except Exception as error:
                app.logger.error('Run %s failed: %s', run_id, type(error).__name__)
                with db() as conn:
                    conn.execute('UPDATE runs SET status=?,result=? WHERE id=?', ('error', json.dumps(dict(error='Processing failed. Retained submission can be retried.')), run_id))

    @app.get('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.get('/health')
    def health():
        with db() as conn:
            conn.execute('SELECT 1').fetchone()
        return jsonify(status='ok')

    @app.get('/api/examples')
    def examples():
        example_dir = Path(__file__).resolve().parent.parent / 'examples'
        return jsonify([json.loads(path.read_text()) for path in sorted(example_dir.glob('*.json'))])

    @app.get('/api/runs')
    def runs():
        with db() as conn:
            rows = conn.execute('SELECT * FROM runs ORDER BY created_at DESC LIMIT 200').fetchall()
            counts = dict(conn.execute('SELECT status,COUNT(*) FROM runs GROUP BY status').fetchall())
        return jsonify(runs=[public(load(row), False) for row in rows], counts=counts, limit=200)

    def validate(payload):
        if not isinstance(payload, dict):
            raise ValueError('Submission must be a JSON object.')
        allowed = {'legal_name', 'country', 'tax_id', 'email', 'address', 'bank'}
        clean = {k: payload.get(k, '') for k in allowed}
        if not isinstance(clean['bank'], dict):
            raise ValueError('bank must be an object.')
        for key in allowed - {'bank'}:
            if not isinstance(clean[key], str) or len(clean[key]) > 500:
                raise ValueError(f'{key} must be text up to 500 characters.')
            clean[key] = clean[key].strip()
        bank = {}
        for key in ['account_holder', 'routing_number', 'account_number', 'iban']:
            value = clean['bank'].get(key, '')
            if not isinstance(value, str) or len(value) > 200:
                raise ValueError(f'bank.{key} must be text up to 200 characters.')
            bank[key] = value.strip()
        clean['bank'] = bank
        return clean

    def evidence(text, filename, raw):
        if len(text) > 100_000:
            raise ValueError('Document text exceeds 100,000 characters.')
        return dict(text=text, filename=Path(filename).name[:200], sha256=hashlib.sha256(raw).hexdigest())

    @app.post('/api/runs')
    def submit():
        documents = {}
        try:
            if request.is_json:
                body = request.get_json()
                if not isinstance(body, dict):
                    raise ValueError('Request must be a JSON object.')
                payload = validate(body.get('submission'))
                docs = body.get('documents', {})
                if not isinstance(docs, dict) or set(docs) - {'tax', 'bank', 'compliance'}:
                    raise ValueError('documents must contain only tax, bank and compliance keys.')
                for kind, text in docs.items():
                    if not isinstance(text, str):
                        raise ValueError('Document content must be text.')
                    documents[kind] = evidence(text, kind + '.txt', text.encode())
            else:
                payload = validate(json.loads(request.form.get('submission', '{}')))
                for kind in ['tax', 'bank', 'compliance']:
                    upload = request.files.get(kind)
                    if not upload or not upload.filename:
                        continue
                    raw = upload.read(2 * 1024 * 1024 + 1)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError('Each document must be at most 2 MB.')
                    suffix = Path(upload.filename).suffix.lower()
                    if suffix == '.pdf':
                        try:
                            reader = PdfReader(io.BytesIO(raw))
                            if reader.is_encrypted or len(reader.pages) > 10:
                                raise ValueError()
                            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
                        except Exception:
                            raise ValueError('PDF could not be read. Use an unencrypted PDF of up to 10 pages or a text export.') from None
                    elif suffix == '.txt':
                        try:
                            text = raw.decode('utf-8')
                        except UnicodeDecodeError:
                            raise ValueError('Text files must use UTF-8 encoding.') from None
                    else:
                        raise ValueError('Upload PDF or UTF-8 .txt files only.')
                    documents[kind] = evidence(text, upload.filename, raw)
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400
        run_id = uuid.uuid4().hex
        with db() as conn:
            conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', (run_id, now(), 'queued', json.dumps(payload), json.dumps(documents), '[]', None))
        executor.submit(process, run_id)
        return jsonify(id=run_id, status='queued'), 202

    @app.get('/api/runs/<run_id>')
    def get_run(run_id):
        with db() as conn:
            row = conn.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            return jsonify(error='Run not found.'), 404
        return jsonify(public(load(row)))

    @app.post('/api/runs/<run_id>/retry')
    def retry(run_id):
        with db() as conn:
            changed = conn.execute("UPDATE runs SET status='queued' WHERE id=? AND status='error'", (run_id,)).rowcount
        if not changed:
            return jsonify(error='Only failed processing runs can be retried.'), 409
        executor.submit(process, run_id)
        return jsonify(id=run_id, status='queued'), 202

    @app.get('/api/runs/<run_id>/report')
    def report(run_id):
        response = get_run(run_id)
        if isinstance(response, tuple):
            return response
        response.headers['Content-Disposition'] = f'attachment; filename="vendorflow-{run_id}.json"'
        return response

    # A new process can resume durable jobs interrupted by a previous shutdown.
    with db() as conn:
        interrupted = conn.execute("SELECT id FROM runs WHERE status IN ('queued','running')").fetchall()
    for row in interrupted:
        executor.submit(process, row['id'])
    return app


if __name__ == '__main__':
    create_app().run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')), threaded=True)
