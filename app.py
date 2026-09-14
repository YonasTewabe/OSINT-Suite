import asyncio
import inspect
import json
import os
import re
import subprocess
import tempfile
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from email_validator import EmailNotValidError, validate_email
from flask import Flask, Response, render_template, request, stream_with_context, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from flask_sqlalchemy import SQLAlchemy

# Load environment variables
load_dotenv()

from user_scanner.core.helpers import (
    ScanConfig,
    load_categories,
    load_modules,
    get_scan_func,
    get_site_name,
    find_category,
    is_loud,
)
from user_scanner.core.result import Result, Status

try:
    from user_scanner.core.helpers import is_valid_email
except ImportError:
    def is_valid_email(email: str) -> bool:  # type: ignore[misc]
        return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Database config
# ─────────────────────────────────────────────────────────────────────────────
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Session lifetime — default 8 hours, override with SESSION_HOURS env var
_session_hours = int(os.getenv('SESSION_HOURS', 8))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=_session_hours)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Convert postgresql:// to postgresql+psycopg:// for psycopg3
db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost/osint_suite')
if db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@app.before_request
def enforce_session_lifetime():
    """Force logout if the absolute session expiry has been reached."""
    if current_user.is_authenticated:
        login_time = session.get('_login_time')
        if login_time is None:
            # Session predates this feature — expire it immediately
            logout_user()
            session.clear()
            return redirect(url_for('login'))
        expires_at = datetime.fromisoformat(login_time) + app.config['PERMANENT_SESSION_LIFETIME']
        if datetime.now(timezone.utc) >= expires_at:
            logout_user()
            session.clear()
            flash('Your session has expired. Please log in again.', 'warning')
            return redirect(url_for('login'))

# ─────────────────────────────────────────────────────────────────────────────
# Database Models
# ─────────────────────────────────────────────────────────────────────────────
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    saved_searches = db.relationship('SavedSearch', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)


class SavedSearch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target = db.Column(db.String(255), nullable=False)
    scan_type = db.Column(db.String(20), nullable=False)  # 'holehe' or 'us'
    mode = db.Column(db.String(20), nullable=True)  # 'email' or 'username' for us
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Create tables
with app.app_context():
    db.create_all()

# ─────────────────────────────────────────────────────────────────────────────
# Auth Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            flash('All fields are required.', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
            return redirect(url_for('register'))

        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            session.permanent = True
            session['_login_time'] = datetime.now(timezone.utc).isoformat()
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ─────────────────────────────────────────────────────────────────────────────
# Search History Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/saved')
@login_required
def saved_searches():
    searches = SavedSearch.query.filter_by(user_id=current_user.id).order_by(
        SavedSearch.created_at.desc()
    ).all()
    return render_template('saved.html', searches=searches)


@app.route('/save', methods=['POST'])
@login_required
def save_search():
    target = request.form.get('target', '').strip()
    scan_type = request.form.get('scan_type', '').strip()
    mode = request.form.get('mode', '').strip()

    if not target or not scan_type:
        return {'ok': False, 'error': 'Missing required fields.'}, 400

    existing = SavedSearch.query.filter_by(
        user_id=current_user.id,
        target=target,
        scan_type=scan_type
    ).first()

    if not existing:
        saved = SavedSearch(
            user_id=current_user.id,
            target=target,
            scan_type=scan_type,
            mode=mode if mode else None
        )
        db.session.add(saved)
        db.session.commit()

    searches = SavedSearch.query.filter_by(user_id=current_user.id).order_by(
        SavedSearch.created_at.desc()
    ).all()
    return {'ok': True, 'searches': _serialize_searches(searches)}


@app.route('/saved/delete/<int:search_id>', methods=['POST'])
@login_required
def delete_saved(search_id):
    search = SavedSearch.query.filter_by(id=search_id, user_id=current_user.id).first()
    if search:
        db.session.delete(search)
        db.session.commit()
    searches = SavedSearch.query.filter_by(user_id=current_user.id).order_by(
        SavedSearch.created_at.desc()
    ).all()
    return {'ok': True, 'searches': _serialize_searches(searches)}


def _serialize_searches(searches):
    return [
        {
            'id': s.id,
            'target': s.target,
            'scan_type': s.scan_type,
            'mode': s.mode or '',
        }
        for s in searches
    ]



def save_search_db(target, scan_type, mode):
    """Save a search to database (only for logged-in users)."""
    if current_user.is_authenticated:
        existing = SavedSearch.query.filter_by(
            user_id=current_user.id,
            target=target,
            scan_type=scan_type
        ).first()
        if not existing:
            saved = SavedSearch(
                user_id=current_user.id,
                target=target,
                scan_type=scan_type,
                mode=mode if mode else None
            )
            db.session.add(saved)
            db.session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Holehe helpers
# ─────────────────────────────────────────────────────────────────────────────

# Matches lines like: [+] Twitter  [-] Adobe  [x] Amazon
LINE_RE = re.compile(r"^\[([+\-x])\]\s+(.*)$")


def check_email(email: str, check_dns: bool = True):
    """Validate email syntax (and optionally MX records). Returns (normalized, error)."""
    try:
        result = validate_email(email, check_deliverability=check_dns)
        return result.normalized, None
    except EmailNotValidError as e:
        return None, f"Invalid email: {e}"


def _get_holehe_cmd(email: str, only_used: bool = True) -> list[str]:
    """Build Holehe command using sys.executable to avoid Windows Application Control blocking holehe.exe."""
    py_code = "import sys; from holehe.core import main; sys.argv = ['holehe'] + sys.argv[1:]; main()"
    cmd = [sys.executable, "-c", py_code, email, "-C"]
    if only_used:
        cmd.append("--only-used")
    return cmd


def run_holehe(email: str, only_used: bool = True):
    """Run the holehe CLI and parse its output into structured rows."""
    cmd = _get_holehe_cmd(email, only_used=only_used)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, cwd=tmpdir
            )
    except subprocess.TimeoutExpired:
        return [], "Lookup timed out. Try again."

    rows = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if "Email used" in line and "Email not used" in line:
            continue
        match = LINE_RE.match(line)
        if match:
            status, site = match.groups()
            site = site.strip()
            if "[" in site or "]" in site:
                continue
            rows.append({"status": status, "site": site})

    error = None
    if result.returncode != 0 and not rows:
        error = result.stderr.strip() or "holehe returned no output."

    return rows, error


# ─────────────────────────────────────────────────────────────────────────────
# User-Scanner helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _us_scan_all(target: str, is_email: bool, no_nsfw: bool, allow_loud: bool):
    """
    Async generator that yields (Result, done_count, total_count) tuples
    as each module completes.
    """
    categories = load_categories(is_email=is_email, no_nsfw=no_nsfw)
    all_modules = []
    for cat_name, cat_path in categories.items():
        for module in load_modules(cat_path):
            all_modules.append((cat_name.capitalize(), module))

    sem: asyncio.Semaphore = asyncio.Semaphore(40)
    queue: asyncio.Queue = asyncio.Queue()

    async def worker(cat_name: str, module):
        async with sem:
            site_name = get_site_name(module)
            func = get_scan_func(module)
            params = {
                "site_name": site_name.capitalize(),
                "username": target,
                "category": cat_name,
                "is_email": is_email,
            }
            if not func:
                await queue.put(Result.error(f"{site_name} has no validate_ function", **params))
                return
            if not allow_loud and is_loud(site_name, is_email=is_email):
                await queue.put(Result.skipped().update(**params))
                return
            try:
                if inspect.iscoroutinefunction(func):
                    result = await func(target)
                else:
                    result = await asyncio.to_thread(func, target)
            except Exception as e:
                result = Result.error(e)
            result.update(**params)
            await queue.put(result)

    tasks = [asyncio.create_task(worker(cat, mod)) for cat, mod in all_modules]
    total = len(tasks)
    done  = 0

    while done < total:
        result = await queue.get()
        done  += 1
        yield result, done, total

    await asyncio.gather(*tasks, return_exceptions=True)


def _us_result_to_dict(r) -> dict:
    return {
        "status":      r.status.to_label(r.is_email),
        "status_code": r.status.value,   # 0=found/registered 1=not found 2=error 3=skipped
        "site_name":   r.site_name or "",
        "category":    r.category  or "",
        "url":         r.url       or "",
        "reason":      r.get_reason(),
        "extra":       r.extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Holehe
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    saved_searches = []
    if current_user.is_authenticated:
        saved_searches = SavedSearch.query.filter_by(
            user_id=current_user.id
        ).order_by(SavedSearch.created_at.desc()).all()

    # Check for query params to auto-start search from saved page
    auto_target = request.args.get('target', '').strip()
    auto_type = request.args.get('type', '').strip()
    auto_mode = request.args.get('mode', '').strip()

    return render_template("index.html", saved_searches=saved_searches,
                           auto_target=auto_target, auto_type=auto_type, auto_mode=auto_mode)


@app.route("/holehe/scan")
def holehe_scan():
    """
    SSE stream endpoint for holehe.
    Query params:
      email      – email address to scan
      only_used  – '1' to return only accounts found
    """
    email     = request.args.get("email", "").strip()
    only_used = request.args.get("only_used", "0") == "1"

    def _err(msg):
        def gen():
            yield "data: " + json.dumps({"type": "error", "message": msg}) + "\n\n"
        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    if not email:
        return _err("Please enter an email address.")

    normalized, validation_error = check_email(email)
    if validation_error:
        return _err(validation_error)

    @stream_with_context
    def generate():
        yield "data: " + json.dumps({"type": "start", "email": normalized}) + "\n\n"

        cmd = _get_holehe_cmd(normalized, only_used=only_used)

        tmpdir_obj = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmpdir = tmpdir_obj.name
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=tmpdir,
            )
            rows = []
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                if "Email used" in line and "Email not used" in line:
                    continue
                match = LINE_RE.match(line)
                if not match:
                    continue
                status, site = match.groups()
                site = site.strip()
                if "[" in site or "]" in site:
                    continue
                row = {"status": status, "site": site}
                rows.append(row)
                yield "data: " + json.dumps({"type": "result", "row": row}) + "\n\n"

            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            if proc.returncode != 0 and not rows:
                stderr = proc.stderr.read().strip()
                yield "data: " + json.dumps({"type": "error", "message": stderr or "holehe returned no output."}) + "\n\n"
                return

        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
                proc.wait()
            yield "data: " + json.dumps({"type": "error", "message": "Lookup timed out."}) + "\n\n"
            return
        except GeneratorExit:
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()
            return
        finally:
            if proc:
                if proc.stdout: proc.stdout.close()
                if proc.stderr: proc.stderr.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            tmpdir_obj.cleanup()

        yield "data: " + json.dumps({"type": "done", "email": normalized, "only_used": only_used}) + "\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/download/<fmt>", methods=["POST"])
def download(fmt):
    email = request.form.get("email", "unknown").strip()
    scope = request.form.get("scope", "full")
    try:
        rows = json.loads(request.form.get("data", "[]"))
    except json.JSONDecodeError:
        rows = []

    found    = [r["site"] for r in rows if r.get("status") == "+"]
    notfound = [r["site"] for r in rows if r.get("status") == "-"]
    errored  = [r["site"] for r in rows if r.get("status") not in ("+", "-")]
    timestamp  = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    safe_email = re.sub(r"[^a-zA-Z0-9]+", "_", email)
    filename   = f"holehe_report_{safe_email}_{timestamp}.{fmt}"

    if fmt == "json":
        payload = {"email": email, "found": found}
        if scope == "full":
            payload["not_found"]            = notfound
            payload["rate_limited_or_error"] = errored
        content  = json.dumps(payload, indent=2)
        mimetype = "application/json"
    else:  # txt
        lines = [f"Holehe report for: {email}", "", f"Found ({len(found)}):"]
        lines += [f"  - {s}" for s in found] or ["  (none)"]
        if scope == "full":
            lines += ["", f"Not Found ({len(notfound)}):"]
            lines += [f"  - {s}" for s in notfound] or ["  (none)"]
            lines += ["", f"Rate Limited / Error ({len(errored)}):"]
            lines += [f"  - {s}" for s in errored] or ["  (none)"]
        content  = "\n".join(lines)
        mimetype = "text/plain"

    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes — User Scanner
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/us")
def us_index():
    """Redirect to the main page with the user-scanner tab pre-selected."""
    from flask import redirect, url_for
    return redirect(url_for("index") + "#us")


@app.route("/us/scan")
def us_scan():
    """
    SSE stream endpoint for user-scanner.
    Query params:
      target      – email address or username
      mode        – 'email' | 'username'
      no_nsfw     – '1' to exclude adult content
      allow_loud  – '1' to include notifying (loud) modules
    """
    target     = request.args.get("target", "").strip()
    mode       = request.args.get("mode", "email").strip()
    no_nsfw    = request.args.get("no_nsfw", "0") == "1"
    allow_loud = request.args.get("allow_loud", "0") == "1"
    is_email   = (mode == "email")

    if not target:
        def _err():
            yield "data: " + json.dumps({"type": "error", "message": "Please enter a target."}) + "\n\n"
        return Response(stream_with_context(_err()), mimetype="text/event-stream")

    if is_email and not is_valid_email(target):
        def _err():
            yield "data: " + json.dumps({"type": "error", "message": f"'{target}' is not a valid email address."}) + "\n\n"
        return Response(stream_with_context(_err()), mimetype="text/event-stream")

    @stream_with_context
    def generate():
        yield "data: " + json.dumps({"type": "start", "target": target, "mode": mode}) + "\n\n"

        # Track counts for history
        found_count = 0
        total_count = 0

        loop = asyncio.new_event_loop()
        try:
            agen = _us_scan_all(target, is_email, no_nsfw, allow_loud)
            while True:
                try:
                    result, done, total = loop.run_until_complete(agen.__anext__())
                    data = _us_result_to_dict(result)
                    data["type"]  = "result"
                    data["done"]  = done
                    data["total"] = total

                    # Track found count (status_code 0 = found)
                    total_count = total
                    if data.get("status_code") == 0:
                        found_count += 1

                    yield "data: " + json.dumps(data) + "\n\n"
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/us/download/<fmt>", methods=["POST"])
def us_download(fmt):
    target = request.form.get("target", "unknown").strip()
    mode   = request.form.get("mode", "email").strip()
    scope  = request.form.get("scope", "full")
    try:
        rows = json.loads(request.form.get("data", "[]"))
    except json.JSONDecodeError:
        rows = []

    is_email       = (mode == "email")
    found_label    = "Registered" if is_email else "Found"
    notfound_label = "Not Registered" if is_email else "Not Found"

    found    = [r for r in rows if r.get("status") == found_label]
    notfound = [r for r in rows if r.get("status") == notfound_label]
    errored  = [r for r in rows if r.get("status") not in (found_label, notfound_label)]

    export_rows = found if scope == "found" else rows

    timestamp   = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    safe_target = re.sub(r"[^a-zA-Z0-9]+", "_", target)
    filename    = f"us_report_{safe_target}_{timestamp}.{fmt}"

    target_label = "Email" if is_email else "Username"

    if fmt == "json":
        payload = {
            "target": target,
            "mode":   mode,
            "found":  [{"site": r["site_name"], "url": r.get("url", ""), "category": r.get("category", "")} for r in found],
        }
        if scope == "full":
            payload["not_found"] = [r["site_name"] for r in notfound]
            payload["errors"]    = [{"site": r["site_name"], "reason": r.get("reason", "")} for r in errored]
        content  = json.dumps(payload, indent=2)
        mimetype = "application/json"

    elif fmt == "csv":
        def esc(v):
            return '"' + str(v).replace('"', '""') + '"'
        lines = ["site_name,category,status,url,reason"]
        for r in export_rows:
            lines.append(",".join([
                esc(r.get("site_name", "")),
                esc(r.get("category", "")),
                esc(r.get("status", "")),
                esc(r.get("url", "")),
                esc(r.get("reason", "")),
            ]))
        content  = "\n".join(lines)
        mimetype = "text/csv"

    else:  # txt
        lines = [
            f"User Scanner report for {target_label}: {target}",
            f"Scan mode: {mode}",
            "",
            f"Found ({len(found)}):",
        ]
        lines += [f"  [{r.get('category','')}] {r['site_name']}  {r.get('url','')}" for r in found] or ["  (none)"]
        if scope == "full":
            lines += ["", f"Not Found ({len(notfound)}):"]
            lines += [f"  {r['site_name']}" for r in notfound] or ["  (none)"]
            lines += ["", f"Errors / Skipped ({len(errored)}):"]
            lines += [f"  {r['site_name']}: {r.get('reason','')}" for r in errored] or ["  (none)"]
        content  = "\n".join(lines)
        mimetype = "text/plain"

    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def init_telegram_bot():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        print("[OSINT Suite] TELEGRAM_BOT_TOKEN not configured. Skipping embedded Telegram bot.", flush=True)
        return
    # In debug/reload mode, only start in the reloaded worker process
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    try:
        print("[OSINT Suite] Found TELEGRAM_BOT_TOKEN. Launching embedded bot...", flush=True)
        from telegram_bot import start_embedded_bot
        start_embedded_bot(bot_token)
    except ImportError as e:
        print(f"[OSINT Suite] Telegram bot dependencies not installed: {e}", flush=True)
        app.logger.warning("Telegram bot dependencies not installed (%s). Run pip install -r requirements.txt", e)
    except Exception as e:
        print(f"[OSINT Suite] Failed to start embedded Telegram bot: {e}", flush=True)
        app.logger.warning("Failed to start embedded Telegram bot: %s", e)


# Start embedded bot on app load so it runs under both `python app.py` and WSGI servers like Gunicorn (Render/production)
init_telegram_bot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
