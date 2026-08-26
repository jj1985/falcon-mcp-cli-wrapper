"""Tests for the browser-login flow and credential profiles (fully offline).

The login server tests run the real loopback HTTP server with a stub validator,
exercising the same code path the browser uses.
"""

import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from falcon_mcp_cli import auth, cli, core


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("FALCON_CLIENT_ID", raising=False)
    monkeypatch.delenv("FALCON_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("FALCON_CLI_PROFILE", raising=False)
    return tmp_path


# --- profile store -----------------------------------------------------------


def test_store_roundtrip_and_permissions():
    auth.save_profile("work", {"client_id": "abc", "client_secret": "s", "base_url": "u"})
    found = auth.get_profile("work")
    assert found is not None
    name, profile = found
    assert name == "work"
    assert profile["client_id"] == "abc"

    if sys.platform != "win32":
        mode = auth.credentials_path().stat().st_mode & 0o777
        assert mode == 0o600


def test_first_profile_becomes_default_and_delete_reassigns():
    auth.save_profile("a", {"client_id": "1", "client_secret": "s", "base_url": "u"})
    auth.save_profile("b", {"client_id": "2", "client_secret": "s", "base_url": "u"})
    assert auth.load_store()["default_profile"] == "a"

    assert auth.delete_profile("a") is True
    assert auth.load_store()["default_profile"] == "b"
    assert auth.get_profile("a") is None
    assert auth.delete_profile("missing") is False


def test_save_profile_make_default():
    auth.save_profile("a", {"client_id": "1", "client_secret": "s", "base_url": "u"})
    auth.save_profile(
        "b", {"client_id": "2", "client_secret": "s", "base_url": "u"}, make_default=True
    )
    assert auth.load_store()["default_profile"] == "b"


# --- credential resolution ---------------------------------------------------


def _args(profile=None):
    parser = cli.build_parser()
    argv = (["--profile", profile] if profile else []) + ["check"]
    return parser.parse_args(argv)


def test_resolution_prefers_environment(monkeypatch):
    auth.save_profile("p", {"client_id": "prof", "client_secret": "s", "base_url": "u"})
    monkeypatch.setenv("FALCON_CLIENT_ID", "env-id")
    monkeypatch.setenv("FALCON_CLIENT_SECRET", "env-secret")
    creds, source = cli.resolve_credentials(_args())
    assert source == "environment"
    assert creds == {}


def test_resolution_explicit_profile_beats_environment(monkeypatch):
    auth.save_profile("p", {"client_id": "prof", "client_secret": "s", "base_url": "u"})
    monkeypatch.setenv("FALCON_CLIENT_ID", "env-id")
    monkeypatch.setenv("FALCON_CLIENT_SECRET", "env-secret")
    creds, source = cli.resolve_credentials(_args(profile="p"))
    assert source == "profile:p"
    assert creds["client_id"] == "prof"


def test_resolution_falls_back_to_default_profile():
    auth.save_profile("only", {"client_id": "x", "client_secret": "s", "base_url": "u"})
    creds, source = cli.resolve_credentials(_args())
    assert source == "profile:only"
    assert creds["client_id"] == "x"


def test_resolution_unknown_profile_errors():
    with pytest.raises(core.UsageError, match="No stored profile"):
        cli.resolve_credentials(_args(profile="ghost"))


def test_resolution_nothing_stored():
    creds, source = cli.resolve_credentials(_args())
    assert creds == {} and source is None


# --- login server ------------------------------------------------------------


def _run_server_in_thread(validator, **kwargs):
    result_holder = {}

    def run():
        result_holder["result"] = auth.run_login_server(
            validator, open_browser=False, timeout=10, **kwargs
        )

    thread = threading.Thread(target=run, daemon=True)
    return thread, result_holder


def _extract_url(capfd) -> str:
    err = capfd.readouterr().err
    for word in err.split():
        if word.startswith("http://127.0.0.1:"):
            return word
    raise AssertionError(f"no login URL printed: {err!r}")


def test_login_server_full_flow(capfd):
    seen = {}

    def validator(candidate):
        seen.update(candidate)

    thread, holder = _run_server_in_thread(validator, profile="work")
    thread.start()
    # wait for the URL to be printed
    url = None
    for _ in range(100):
        try:
            url = _extract_url(capfd)
            break
        except AssertionError:
            thread.join(0.05)
    assert url, "server did not print its URL"
    token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["token"][0]

    # GET the form
    with urllib.request.urlopen(url) as resp:
        page = resp.read().decode()
    assert "Client secret" in page and token in page

    # GET without token is refused
    bare = url.split("?")[0]
    try:
        urllib.request.urlopen(bare)
        raise AssertionError("expected 403")
    except urllib.error.HTTPError as e:
        assert e.code == 403

    # POST with a wrong token is refused
    bad = urllib.parse.urlencode(
        {"token": "nope", "client_id": "i", "client_secret": "s", "region": "us-2"}
    ).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(f"{bare}submit", data=bad))
        raise AssertionError("expected 403")
    except urllib.error.HTTPError as e:
        assert e.code == 403

    # POST the real submission
    good = urllib.parse.urlencode(
        {
            "token": token,
            "client_id": "id-123",
            "client_secret": "sec-456",
            "region": "us-2",
            "profile": "work",
        }
    ).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{bare}submit", data=good)) as resp:
        assert "Signed in" in resp.read().decode()

    thread.join(5)
    assert holder["result"]["profile"] == "work"
    assert seen["base_url"] == auth.REGIONS["us-2"]["api"]

    stored = auth.get_profile("work")
    assert stored is not None
    assert stored[1]["client_id"] == "id-123"
    assert stored[1]["region"] == "us-2"


def test_login_server_validator_rejection_keeps_serving(capfd):
    def validator(candidate):
        return "Bad credentials, try again"

    thread, holder = _run_server_in_thread(validator, profile="default")
    thread.start()
    url = None
    for _ in range(100):
        try:
            url = _extract_url(capfd)
            break
        except AssertionError:
            thread.join(0.05)
    assert url
    token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["token"][0]
    bare = url.split("?")[0]

    body = urllib.parse.urlencode(
        {"token": token, "client_id": "i", "client_secret": "s", "region": "us-1"}
    ).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{bare}submit", data=body)) as resp:
        page = resp.read().decode()
    assert "Bad credentials, try again" in page
    assert auth.get_profile("default") is None  # nothing saved
    assert holder.get("result") is None  # the server keeps waiting for a retry


def test_render_form_lists_all_regions():
    page = auth.render_form("tok", "eu-1")
    for region in auth.REGIONS:
        assert region in page
    assert "selected" in page


# --- CLI integration ---------------------------------------------------------


def test_cli_profiles_json(capsys):
    auth.save_profile(
        "work",
        {"client_id": "abcdefgh", "client_secret": "s", "base_url": "u", "region": "us-1"},
    )
    parser = cli.build_parser()
    args = parser.parse_args(["profiles", "--json"])
    assert args.func(args) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["default_profile"] == "work"
    entry = payload["profiles"][0]
    assert entry["name"] == "work"
    assert "abcdefgh" not in json.dumps(entry)  # client_id is masked
    assert "s" != entry.get("client_secret")  # secret never listed


def test_cli_logout(capsys):
    auth.save_profile("work", {"client_id": "a", "client_secret": "s", "base_url": "u"})
    parser = cli.build_parser()
    args = parser.parse_args(["logout", "work"])
    assert args.func(args) == cli.EXIT_OK
    assert auth.get_profile("work") is None


def test_cli_logout_all(capsys):
    auth.save_profile("a", {"client_id": "1", "client_secret": "s", "base_url": "u"})
    auth.save_profile("b", {"client_id": "2", "client_secret": "s", "base_url": "u"})
    parser = cli.build_parser()
    args = parser.parse_args(["logout", "--all"])
    assert args.func(args) == cli.EXIT_OK
    assert not auth.credentials_path().exists()
