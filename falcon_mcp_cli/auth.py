"""Browser-based login and credential profiles for falcon-cli.

Modeled on the CrowdStrike Foundry CLI's ``foundry login`` UX: the CLI starts a
loopback HTTP server, opens the browser to a local form that deep-links to the
Falcon console's "API clients and keys" page, receives the credentials over the
loopback callback, validates them live against the Falcon API, and stores them
as a named profile.

Unlike Foundry, the public Falcon API has no endpoint that provisions API
clients for third-party CLIs, so the console cannot hand the secret back
automatically — the user creates (or reuses) an API client in the console and
pastes the ID/secret into the local form. Everything else matches: loopback
callback, live validation before anything is saved, named profiles.

Profiles are stored in ``~/.config/falcon-cli/credentials.json`` (or under
``$XDG_CONFIG_HOME`` / ``%APPDATA%``), file mode 0600, directory 0700.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REGIONS: dict[str, dict[str, str]] = {
    "us-1": {
        "api": "https://api.crowdstrike.com",
        "console": "https://falcon.crowdstrike.com",
    },
    "us-2": {
        "api": "https://api.us-2.crowdstrike.com",
        "console": "https://falcon.us-2.crowdstrike.com",
    },
    "eu-1": {
        "api": "https://api.eu-1.crowdstrike.com",
        "console": "https://falcon.eu-1.crowdstrike.com",
    },
    "us-gov-1": {
        "api": "https://api.laggar.gcw.crowdstrike.com",
        "console": "https://falcon.laggar.gcw.crowdstrike.com",
    },
}

DEFAULT_REGION = "us-1"
LOGIN_TIMEOUT_SECONDS = 600

# Validator signature: takes the candidate profile dict, returns an error
# message, or None when the credentials authenticate successfully.
Validator = Callable[[dict[str, Any]], str | None]


# --- profile store -----------------------------------------------------------


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "falcon-cli"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def load_store() -> dict[str, Any]:
    path = credentials_path()
    if not path.exists():
        return {"default_profile": None, "profiles": {}}
    try:
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"default_profile": None, "profiles": {}}
    store.setdefault("default_profile", None)
    store.setdefault("profiles", {})
    return store


def save_store(store: dict[str, Any]) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def save_profile(name: str, profile: dict[str, Any], make_default: bool = False) -> Path:
    store = load_store()
    store["profiles"][name] = profile
    if make_default or store.get("default_profile") is None:
        store["default_profile"] = name
    return save_store(store)


def get_profile(name: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """Return (name, profile) for ``name`` or the default profile, else None."""
    store = load_store()
    resolved = name or store.get("default_profile")
    if resolved and resolved in store["profiles"]:
        return resolved, store["profiles"][resolved]
    return None


def delete_profile(name: str) -> bool:
    store = load_store()
    if name not in store["profiles"]:
        return False
    del store["profiles"][name]
    if store.get("default_profile") == name:
        store["default_profile"] = next(iter(store["profiles"]), None)
    save_store(store)
    return True


# --- login form --------------------------------------------------------------

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>falcon-cli login</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #f6f7f9; color: #1a1d21;
         display: flex; justify-content: center; padding: 3rem 1rem; }}
  .card {{ background: #fff; border: 1px solid #d9dde3; border-radius: 10px;
          padding: 2rem; max-width: 34rem; width: 100%; }}
  h1 {{ font-size: 1.25rem; margin-top: 0; }}
  label {{ display: block; margin: 0.9rem 0 0.25rem; font-weight: 600; font-size: 0.9rem; }}
  input, select {{ width: 100%; padding: 0.5rem; border: 1px solid #c3c9d1;
                  border-radius: 6px; font-size: 0.95rem; box-sizing: border-box; }}
  button {{ margin-top: 1.4rem; width: 100%; padding: 0.6rem; border: 0; border-radius: 6px;
           background: #e5372b; color: #fff; font-size: 1rem; cursor: pointer; }}
  .hint {{ font-size: 0.85rem; color: #5a626d; margin: 0.3rem 0 0; }}
  .error {{ background: #fdecea; border: 1px solid #f5c6c0; color: #a12b20;
           padding: 0.6rem 0.8rem; border-radius: 6px; margin-bottom: 1rem; }}
  a {{ color: #0b6bcb; }}
</style></head><body><div class="card">
<h1>Sign in falcon-cli to CrowdStrike Falcon</h1>
{error}
<p class="hint">Create (or reuse) an API client in the Falcon console under
<strong>Support and resources &rarr; API clients and keys</strong>, grant it the scopes you
need, then paste the credentials here. They are validated against the Falcon API
before being saved locally, and are only ever sent to this local page and to
CrowdStrike.</p>
<p><a id="console-link" href="{console}/api-clients-and-keys" target="_blank" rel="noopener">
Open the Falcon console (API clients and keys) &rarr;</a></p>
<form method="post" action="/submit">
<input type="hidden" name="token" value="{token}">
<label for="region">Region</label>
<select id="region" name="region" onchange="syncLink()">{region_options}</select>
<label for="client_id">Client ID</label>
<input id="client_id" name="client_id" required autocomplete="off" value="{client_id}">
<label for="client_secret">Client secret</label>
<input id="client_secret" name="client_secret" type="password" required autocomplete="off">
<label for="member_cid">Member CID <span class="hint" style="display:inline">(optional, MSSP only)</span></label>
<input id="member_cid" name="member_cid" autocomplete="off" value="{member_cid}">
<label for="profile">Profile name</label>
<input id="profile" name="profile" value="{profile}">
<p class="hint">Profiles let you keep credentials for several tenants; switch with
<code>falcon-cli --profile NAME &hellip;</code></p>
<button type="submit">Validate &amp; save</button>
</form>
<script>
const consoles = {consoles_json};
function syncLink() {{
  const r = document.getElementById("region").value;
  if (consoles[r]) {{
    document.getElementById("console-link").href = consoles[r] + "/api-clients-and-keys";
  }}
}}
syncLink();
</script>
</div></body></html>"""

_SUCCESS_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>falcon-cli login</title>
<style>body { font-family: system-ui, sans-serif; background: #f6f7f9;
display: flex; justify-content: center; padding: 3rem 1rem; }
.card { background: #fff; border: 1px solid #d9dde3; border-radius: 10px;
padding: 2rem; max-width: 34rem; }</style></head><body><div class="card">
<h1>&#10004; Signed in</h1>
<p>Credentials for profile <strong>%s</strong> were validated and saved.
You can close this tab and return to the terminal.</p>
</div></body></html>"""


def render_form(
    token: str,
    region: str,
    error: str | None = None,
    client_id: str = "",
    member_cid: str = "",
    profile: str = "default",
) -> str:
    options = "".join(
        f'<option value="{name}"{" selected" if name == region else ""}>'
        f"{name.upper()} ({info['api']})</option>"
        for name, info in REGIONS.items()
    )
    return _PAGE_TEMPLATE.format(
        error=f'<div class="error">{error}</div>' if error else "",
        token=token,
        console=REGIONS[region]["console"],
        region_options=options,
        client_id=client_id,
        member_cid=member_cid,
        profile=profile,
        consoles_json=json.dumps({name: info["console"] for name, info in REGIONS.items()}),
    )


class _LoginHandler(BaseHTTPRequestHandler):
    """Serves the login form and receives the credential submission."""

    # Injected by run_login_server:
    token: str = ""
    region: str = DEFAULT_REGION
    profile: str = "default"
    validator: Validator = staticmethod(lambda _profile: "validator not configured")
    result: dict[str, Any] | None = None
    done = threading.Event()

    def _send_html(self, body: str, status: int = 200) -> None:
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self._send_html("<h1>404</h1>", status=404)
            return
        query = parse_qs(parsed.query)
        if query.get("token", [None])[0] != type(self).token:
            self._send_html("<h1>403 — bad or missing token</h1>", status=403)
            return
        self._send_html(
            render_form(type(self).token, type(self).region, profile=type(self).profile)
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/submit":
            self._send_html("<h1>404</h1>", status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        fields = {
            key: values[0]
            for key, values in parse_qs(self.rfile.read(length).decode()).items()
        }
        if fields.get("token") != type(self).token:
            self._send_html("<h1>403 — bad or missing token</h1>", status=403)
            return

        region = fields.get("region", type(self).region)
        if region not in REGIONS:
            region = DEFAULT_REGION
        candidate = {
            "client_id": fields.get("client_id", "").strip(),
            "client_secret": fields.get("client_secret", "").strip(),
            "base_url": REGIONS[region]["api"],
            "region": region,
            "member_cid": fields.get("member_cid", "").strip() or None,
        }
        profile_name = fields.get("profile", "").strip() or "default"

        error = None
        if not candidate["client_id"] or not candidate["client_secret"]:
            error = "Client ID and client secret are both required."
        else:
            error = type(self).validator(candidate)

        if error:
            self._send_html(
                render_form(
                    type(self).token,
                    region,
                    error=error,
                    client_id=candidate["client_id"],
                    member_cid=candidate["member_cid"] or "",
                    profile=profile_name,
                )
            )
            return

        save_profile(profile_name, candidate)
        type(self).result = {"profile": profile_name, **candidate}
        self._send_html(_SUCCESS_PAGE % profile_name)
        type(self).done.set()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # keep the terminal quiet; the CLI prints its own status


def run_login_server(
    validator: Validator,
    region: str = DEFAULT_REGION,
    profile: str = "default",
    port: int = 0,
    open_browser: bool = True,
    timeout: float = LOGIN_TIMEOUT_SECONDS,
    on_ready: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Serve the login form on loopback until credentials are saved or timeout.

    Returns the saved profile dict (including its name) or None on timeout.

    ``on_ready`` is called once with the bound login URL as soon as the server
    is listening, before the browser is opened — a deterministic hook for
    callers that need the URL (rather than scraping it from stderr).
    """
    handler = type(
        "BoundLoginHandler",
        (_LoginHandler,),
        {
            "token": secrets.token_urlsafe(24),
            "region": region,
            "profile": profile,
            "validator": staticmethod(validator),
            "result": None,
            "done": threading.Event(),
        },
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    server.timeout = 1.0
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={handler.token}"

    if on_ready is not None:
        on_ready(url)
    print(f"Open this URL in your browser to sign in:\n\n  {url}\n", file=sys.stderr)
    if open_browser:
        import webbrowser

        webbrowser.open(url)

    deadline = time.monotonic() + timeout
    try:
        while not handler.done.is_set() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return handler.result


def make_falcon_validator() -> Validator:
    """Real validator: authenticates the candidate credentials against Falcon."""

    def validate(candidate: dict[str, Any]) -> str | None:
        from falcon_mcp.client import FalconClient

        try:
            client = FalconClient(
                base_url=candidate["base_url"],
                client_id=candidate["client_id"],
                client_secret=candidate["client_secret"],
                member_cid=candidate.get("member_cid"),
                user_agent_comment="falcon-mcp-cli login",
            )
        except ValueError as exc:
            return str(exc)
        if not client.authenticate():
            return client.auth_failure_message()
        return None

    return validate
