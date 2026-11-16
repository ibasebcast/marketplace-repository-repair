"""Ingress UI for repairing Marketplace repository ownership on Home Assistant OS."""

from contextlib import suppress
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from threading import Lock, Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import ProxyHandler, Request, build_opener

from repair import ALARM_DOT_COM_REPOS, REPOSITORY_NAME, STORE_KEY, read_repair, save_repair

INGRESS_PEER = "172.30.32.2"
CONFIG = Path("/homeassistant")
RECOVERY_FILE = Path("/data/core-restart-pending")
STATE_LOCK = Lock()

STYLE = """
:root { color-scheme: light dark; font: 16px/1.55 system-ui, sans-serif; }
body { margin: 0; }
main { max-width: 42rem; margin: 2rem auto; padding: 0 1.25rem 2rem; }
h1 { font-size: 1.8rem; line-height: 1.2; }
h2 { font-size: 1.25rem; }
fieldset, .panel { border: 1px solid #808080; border-radius: .75rem; padding: 1rem; }
form, .panel { margin-block: 1rem; }
label { display: block; margin-block: .8rem; }
input[type=text] { display: block; box-sizing: border-box; width: 100%; padding: .5rem; font: inherit; }
button { font: inherit; padding: .5rem .85rem; border-radius: .45rem; cursor: pointer; }
.primary { background: #006b98; color: white; border: 1px solid #006b98; }
code { overflow-wrap: anywhere; }
"""


@dataclass(frozen=True)
class Plan:
    old_repo: str
    new_repo: str
    domain: str
    installed: bool

    @property
    def next_step(self):
        return "Update" if self.installed else "Install"


def supervisor(action, *, method="POST"):
    """Call a Core endpoint on Supervisor, independently of the Core UI."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ValueError("The Supervisor token is unavailable. Restart the repair app.")
    request = Request(
        f"http://supervisor/core/{action}", method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=b"{}" if method == "POST" else None,
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=300) as response:
            result = json.load(response)
    except HTTPError as error:
        raise ValueError(f"Supervisor could not {action} Core (HTTP {error.code}).") from error
    if result.get("result") != "ok":
        raise ValueError(f"Supervisor could not {action} Core.")
    return result.get("data", {})


def inspect_repair(old_repo, new_repo):
    """Check the repository names and shared domain using the CLI repair engine."""
    for name in (old_repo, new_repo):
        if not REPOSITORY_NAME.fullmatch(name):
            raise ValueError("Enter each GitHub repository as owner/name.")
    if old_repo.casefold() == new_repo.casefold():
        raise ValueError("Choose two different repositories.")
    storage = CONFIG / ".storage" / STORE_KEY
    args = SimpleNamespace(config=CONFIG, old_repo=old_repo, new_repo=new_repo)
    original, (domain, updated, installed) = read_repair(storage, args)
    backup = storage.with_name(storage.name + ".before-repository-repair")
    if updated is not None and backup.exists():
        raise ValueError("A previous repair backup exists. See the app documentation before continuing.")
    return Plan(old_repo, new_repo, domain, installed), storage, original, updated


class AppState:
    def __init__(self):
        self.csrf = secrets.token_urlsafe()
        self.pending = None
        self.preview_id = None
        self.busy = False
        self.notice = ""

    def resume_core(self):
        """Retry a pending Core start after the app itself is restarted."""
        if not RECOVERY_FILE.exists():
            return
        try:
            supervisor("start")
            RECOVERY_FILE.unlink()
        except (OSError, ValueError) as error:
            self.notice = f"Core restart failed. Restart this app to retry. {error}"
        else:
            self.notice = "Core was started after an interrupted repair. Preview the repair again to check its status."

    def run(self, plan):
        """Keep serving the repair after the browser loses its Core connection."""
        message = ""
        try:
            current, _, _, updated = inspect_repair(plan.old_repo, plan.new_repo)
            if current != plan or updated is None:
                raise ValueError("The conflict changed after the preview. Preview it again.")
            # Record recovery before requesting shutdown. A restarted app can
            # bring Core back even if this process was interrupted.
            RECOVERY_FILE.touch(exist_ok=False)
            try:
                supervisor("stop")
                current, storage, original, updated = inspect_repair(plan.old_repo, plan.new_repo)
                if current != plan or updated is None:
                    raise ValueError("The conflict changed during shutdown. Preview it again.")
                save_repair(storage, original, updated)
                message = f"Repair complete. In Marketplace, choose {plan.next_step} for {plan.new_repo}."
            finally:
                supervisor("start")
                RECOVERY_FILE.unlink()
        except (OSError, ValueError) as error:
            restart_note = " A Core restart is pending. Restart the repair app to retry." if RECOVERY_FILE.exists() else ""
            message = f"{message or 'Repair stopped.'} {error}{restart_note}"
        finally:
            with STATE_LOCK:
                self.notice = message or "The repair ended unexpectedly. Check the app log."
                self.busy = False
            print(self.notice)


STATE = AppState()


def page(*, notice="", preview=None, preview_id=None, busy=False):
    token = f'<input type="hidden" name="csrf" value="{escape(STATE.csrf)}">'
    content = '<h1>Marketplace Repository Repair</h1>'
    if notice:
        content += f'<p class="panel" role="status">{escape(notice)}</p>'
    if busy:
        content += "<p>Home Assistant may briefly disconnect. Once it is back online, reopen this app to see the result.</p>"
    elif preview is not None:
        content += f"""
        <section class="panel">
          <h2>Review the repair</h2>
          <p>Integration <code>{escape(preview.domain)}</code></p>
          <p>Clear the ownership claim from <code>{escape(preview.old_repo)}</code>.
          When Home Assistant is back online, choose <strong>{preview.next_step}</strong>
          for <code>{escape(preview.new_repo)}</code> in Marketplace.</p>
          <p>Home Assistant will briefly go offline. The repair saves a copy of Marketplace's metadata before changing it.</p>
          <form method="post">
            {token}
            <input type="hidden" name="action" value="apply">
            <input type="hidden" name="preview" value="{escape(preview_id)}">
            <label><input type="checkbox" name="backup" value="yes" required> I made a full Home Assistant backup.</label>
            <button class="primary">Repair and restart Home Assistant</button>
            <a href="./">Cancel</a>
          </form>
        </section>"""
    else:
        content += f"""
        <p>Use this when Marketplace says the integration folder belongs to another repository.
        Add the replacement to Marketplace first. If Marketplace offers <strong>Delete and uninstall</strong>, choose Cancel.</p>
        <section class="panel">
          <h2>Alarm.com</h2>
          <p>Switch from the original integration to the maintained version.</p>
          <form method="post">
            {token}<input type="hidden" name="action" value="alarm">
            <button class="primary">Preview Alarm.com repair</button>
          </form>
        </section>
        <details>
          <summary>Repair another integration</summary>
          <form method="post">
            {token}<input type="hidden" name="action" value="preview">
            <label>Repository named in the error
              <input type="text" name="old" maxlength="200" placeholder="owner/repository" required>
            </label>
            <label>Replacement repository
              <input type="text" name="new" maxlength="200" placeholder="owner/repository" required>
            </label>
            <button>Preview repair</button>
          </form>
        </details>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Marketplace Repository Repair</title><style>{STYLE}</style>
    </head><body><main>{content}</main></body></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    def show(self, status=200, **kwargs):
        data = page(**kwargs)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'self'")
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)

    def allowed(self):
        if self.client_address[0] != INGRESS_PEER:
            self.send_error(403)
            return False
        if self.path != "/":
            self.send_error(404)
            return False
        return True

    def do_GET(self):
        if self.allowed():
            with STATE_LOCK:
                self.show(notice=STATE.notice, busy=STATE.busy)

    def do_POST(self):
        if not self.allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 2048:
                raise ValueError
            form = parse_qs(self.rfile.read(length).decode(), max_num_fields=5)
        except ValueError:
            self.send_error(400)
            return
        if form.get("csrf") != [STATE.csrf]:
            self.send_error(403)
            return

        with STATE_LOCK:
            if STATE.busy:
                self.show(busy=True)
                return
            action = form.get("action", [""])[0]
            if action == "apply":
                if (
                    STATE.pending is None or form.get("preview") != [STATE.preview_id]
                    or form.get("backup") != ["yes"]
                ):
                    self.show(400, notice="Preview the repair again and confirm you have a backup.")
                    return
                plan = STATE.pending
                STATE.pending = STATE.preview_id = None
                STATE.busy = True
                STATE.notice = "Repair started."
                # Queue the work before responding. It does not depend on the
                # browser or ingress connection staying alive.
                Thread(target=STATE.run, args=(plan,)).start()
                self.show(202, notice=STATE.notice, busy=True)
                return

            if action == "alarm":
                old_repo, new_repo = ALARM_DOT_COM_REPOS
            elif action == "preview":
                old_repo = form.get("old", [""])[0].strip()
                new_repo = form.get("new", [""])[0].strip()
            else:
                self.send_error(400)
                return
            STATE.pending = STATE.preview_id = None
            try:
                if RECOVERY_FILE.exists():
                    raise ValueError("A Core restart is pending. Restart the repair app before continuing.")
                plan, _, _, updated = inspect_repair(old_repo, new_repo)
            except (OSError, ValueError) as error:
                self.show(400, notice=str(error))
                return
            if updated is None:
                self.show(notice=f"No ownership change is needed. In Marketplace, choose {plan.next_step} for {plan.new_repo} if available.")
                return
            STATE.pending = plan
            STATE.preview_id = secrets.token_urlsafe()
            self.show(preview=plan, preview_id=STATE.preview_id)

    def log_message(self, format, *args):
        """Avoid logging ingress routes or any posted data."""


if __name__ == "__main__":
    STATE.resume_core()
    with ThreadingHTTPServer(("0.0.0.0", 8099), Handler) as server:
        print("Marketplace Repository Repair is ready.")
        server.serve_forever()
