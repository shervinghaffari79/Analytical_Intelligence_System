"""Optional shared-password gate for internet-facing deployments."""
from __future__ import annotations

import base64
import os
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.backend]


@pytest.fixture()
def client():
    from data_formulator.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_password_configured_changes_nothing(client):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DF_ACCESS_PASSWORD", None)
        assert client.get("/api/app-config").status_code == 200


def test_password_required_on_api_and_pages(client):
    with patch.dict(os.environ, {"DF_ACCESS_PASSWORD": "s3cret-pässword"}):
        for path in ("/api/app-config", "/api/agent/list-global-models", "/chat", "/"):
            resp = client.get(path)
            assert resp.status_code == 401, path
            assert "Basic" in resp.headers["WWW-Authenticate"]


def test_wrong_password_rejected_right_one_accepted(client):
    with patch.dict(os.environ, {"DF_ACCESS_PASSWORD": "s3cret-pässword"}):
        assert client.get("/api/app-config", headers=_basic("df", "nope")).status_code == 401
        assert client.get("/api/app-config", headers=_basic("df", "")).status_code == 401
        # any username works; non-ASCII passwords are compared as UTF-8
        assert client.get("/api/app-config", headers=_basic("anyone", "s3cret-pässword")).status_code == 200


def test_bearer_header_is_not_accepted(client):
    with patch.dict(os.environ, {"DF_ACCESS_PASSWORD": "pw"}):
        resp = client.get("/api/app-config", headers={"Authorization": "Bearer pw"})
        assert resp.status_code == 401


def test_port_defaults_from_environment():
    from data_formulator.app import parse_args

    with patch.dict(os.environ, {"PORT": "5000"}), patch("sys.argv", ["data_formulator"]):
        assert parse_args().port == 5000
    with patch.dict(os.environ, {}, clear=False), patch("sys.argv", ["data_formulator", "-p", "6001"]):
        os.environ.pop("PORT", None)
        assert parse_args().port == 6001


def test_debugger_is_never_enabled_for_a_network_facing_server():
    from data_formulator.app import _debug_enabled

    assert _debug_enabled(True, "127.0.0.1") and _debug_enabled(True, "localhost")
    assert not _debug_enabled(True, "0.0.0.0")
    assert not _debug_enabled(True, "203.0.113.7")
    assert not _debug_enabled(False, "127.0.0.1")
