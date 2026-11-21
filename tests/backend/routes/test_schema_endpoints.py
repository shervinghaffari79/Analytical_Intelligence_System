"""Schema-file API: template -> validate -> save -> get -> delete, on the built-in sample source."""
from __future__ import annotations

import io
import json

import pytest

pytestmark = [pytest.mark.backend]

SOURCE = "sample_datasets"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_FORMULATOR_HOME", str(tmp_path))
    from data_formulator.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def post(client, path, payload=None, **kw):
    resp = client.post(f"/api/connectors/schema/{path}", json=payload, headers={"X-Workspace-Id": "schema_test", "X-Identity-Id": "browser:schematest"}, **kw)
    body = json.loads(resp.data)
    assert resp.status_code == 200 and body["status"] == "success", body
    return body["data"]


def test_template_then_upload_round_trip(client):
    tpl = post(client, "template", {"connector_id": SOURCE})
    assert tpl["filename"] == "sample_datasets.schema.yaml" and "tables:" in tpl["content"]

    # an untouched template is valid and documents nothing destructive
    report = post(client, "validate", {"connector_id": SOURCE, "content": tpl["content"], "filename": "t.yaml"})
    assert report["ok"] and report["matched_tables"] == report["tables_in_catalog"] > 0

    yaml_text = ('description: "US data"\nrules: ["Percentages are 0-100"]\ntables:\n'
                 '  "US Income":\n    description: "Household income by state and bracket"\n'
                 '    grain: "one row per state and income bracket"\n    columns:\n      pct: {description: "share of households", unit: "%"}\n'
                 '  "Ghost Table": "does not exist"\n')
    rep = post(client, "validate", {"connector_id": SOURCE, "content": yaml_text, "filename": "s.yaml"})
    assert rep["ok"] and rep["matched_tables"] == 1
    assert any("Ghost Table" in w["path"] for w in rep["warnings"])

    assert post(client, "get", {"connector_id": SOURCE})["present"] is False
    saved = post(client, "save", {"connector_id": SOURCE, "content": yaml_text, "filename": "s.yaml"})
    assert saved["saved"] and saved["report"]["matched_tables"] == 1

    info = post(client, "get", {"connector_id": SOURCE})
    assert info["present"] and info["filename"] == "s.yaml" and info["report"]["matched_tables"] == 1 and info["rules"] == 1

    # the saved text now comes back in the template (round trip) and in the catalog
    again = post(client, "template", {"connector_id": SOURCE})["content"]
    assert "Household income by state and bracket" in again and "Percentages are 0-100" in again
    assert post(client, "delete", {"connector_id": SOURCE})["removed"] is True
    assert post(client, "get", {"connector_id": SOURCE})["present"] is False


def test_bad_files_are_reported_not_saved(client):
    broken = post(client, "save", {"connector_id": SOURCE, "content": "tables: [unclosed", "filename": "b.yaml"})
    assert broken["saved"] is False and "Invalid YAML" in broken["report"]["errors"][0]["message"]

    wrong = post(client, "save", {"connector_id": SOURCE, "content": "tables:\n  Nothing Here: x\n", "filename": "w.yaml"})
    assert wrong["saved"] is False and "None of the tables" in wrong["report"]["errors"][0]["message"]
    assert post(client, "get", {"connector_id": SOURCE})["present"] is False


def test_multipart_upload_and_cp1256_csv(client):
    csv_text = "table,column,description\nUS Income,pct,درصد خانوارها\n"
    resp = client.post("/api/connectors/schema/save", headers={"X-Workspace-Id": "schema_test", "X-Identity-Id": "browser:schematest"},
                       data={"connector_id": SOURCE, "file": (io.BytesIO(csv_text.encode("cp1256")), "dictionary.csv")},
                       content_type="multipart/form-data")
    data = json.loads(resp.data)["data"]
    assert data["saved"], data
    tpl = post(client, "template", {"connector_id": SOURCE})["content"]
    assert "درصد خانوارها" in tpl, "Persian text from a Windows-1256 CSV survives"
