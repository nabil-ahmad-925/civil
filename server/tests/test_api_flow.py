from .conftest import upload, upload_and_ingest
from .pdfs import make_pdf


def refs_by_label(repo, manual=None):
    docs = {d.id: d.manual for d in repo.documents.values()}
    return {r.target_label: r for r in repo.refs.values() if manual is None or docs[r.document_id] == manual}


def test_health_and_unknown_route_error_shape(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/nope").json() == {"error": "Not Found"}


def test_upload_saves_the_file_and_returns_a_camel_case_record(client, repo, pdfs):
    res = upload(client, "PPM", pdfs["PPM"], "ppm.pdf", version="2024")
    body = res.json()
    assert res.status_code == 201
    assert body["manual"] == "PPM" and body["status"] == "uploaded" and body["originalName"] == "ppm.pdf"
    assert body["version"] == "2024" and body["sizeBytes"] == len(pdfs["PPM"]) and len(body["sha256"]) == 64
    assert "storagePath" not in body
    # the PDF is in the bucket, and nothing has been chunked yet
    assert list(repo.files.values()) == [pdfs["PPM"]]
    assert repo.nodes == {}
    assert [d["id"] for d in client.get("/api/documents").json()] == [body["id"]]


def test_blank_version_is_recorded_as_not_specified(client, pdfs):
    assert upload(client, "PPM", pdfs["PPM"]).json()["version"] == "Not specified"


def test_upload_rejections(client, pdfs):
    assert upload(client, "PPM", b"not a pdf at all").json() == {"error": "File is not a PDF."}
    assert upload(client, "PPM", b"").json() == {"error": "File is empty."}
    assert upload(client, "PPM", pdfs["PPM"], name="  ").json() == {"error": "Filename is required."}
    assert upload(client, "XYZ", pdfs["PPM"]).status_code == 422


def test_the_same_file_can_be_uploaded_again(client, repo, pdfs):
    first = upload(client, "PPM", pdfs["PPM"], "ppm.pdf")
    second = upload(client, "PPM", pdfs["PPM"], "ppm.pdf")
    other_manual = upload(client, "HDM", pdfs["PPM"], "ppm.pdf")
    assert (first.status_code, second.status_code, other_manual.status_code) == (201, 201, 201)
    ids = {first.json()["id"], second.json()["id"], other_manual.json()["id"]}
    assert len(ids) == 3 and len(repo.files) == 3
    assert {d["sha256"] for d in client.get("/api/documents").json()} == {first.json()["sha256"]}


def test_ingest_saves_chunks_refs_and_vectors(client, repo, pdfs):
    doc = upload_and_ingest(client, "PPM", pdfs["PPM"])
    assert doc["status"] == "ingested" and doc["error"] is None
    assert doc["summary"]["nodes"] == 5 and doc["summary"]["cross_refs"] == 3

    assert sorted(n.section for n in repo.nodes.values()) == ["2", "2.1", "2.1.3", "2.2", "2.3"]
    assert all(n.embedding is not None for n in repo.nodes.values())

    refs = refs_by_label(repo)
    node_2_1_3 = next(n for n in repo.nodes.values() if n.section == "2.1.3")
    # same manual: linked right away by the internal pass
    assert (refs["PPM 2.1.3"].status, refs["PPM 2.1.3"].resolved_by) == ("resolved", "internal_pass")
    assert refs["PPM 2.1.3"].target_node_id == node_2_1_3.id
    # other manual not uploaded yet: waits
    assert refs["CADD 2.6"].status == "awaiting_cross"
    # tables have no section to look up
    assert refs["PPM Table 2.4A"].status == "unresolved"


def test_references_link_across_manuals_as_each_manual_is_ingested(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    assert refs_by_label(repo)["CADD 2.6"].status == "awaiting_cross"

    # CADD arrives: PPM's reference to it settles without touching PPM again
    cadd = upload_and_ingest(client, "CADD", pdfs["CADD"])
    ref = refs_by_label(repo)["CADD 2.6"]
    cadd_26 = next(n for n in repo.nodes.values() if n.manual == "CADD" and n.section == "2.6")
    assert (ref.status, ref.resolved_by, ref.target_node_id) == ("resolved", "cross_manual_pass", cadd_26.id)
    assert cadd["summary"]["cross_manual_pass"]["resolved"] == 1

    hdm = upload_and_ingest(client, "HDM", pdfs["HDM"])
    refs = refs_by_label(repo, "HDM")
    assert refs["PPM 2.2"].status == "resolved"          # section exists in PPM
    assert refs["CADD 9.9"].status == "unresolved"       # CADD is in, but has no section 9.9
    assert refs["HDM 301.5"].status == "resolved"        # same manual
    assert hdm["summary"]["cross_manual_pass"] == {"resolved": 2, "unresolved": 1, "waiting_for_other_manual": 0}


def test_reingesting_replaces_nodes_and_relinks_other_manuals(client, repo, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    upload_and_ingest(client, "HDM", pdfs["HDM"])
    count = len(repo.nodes)
    old_target = refs_by_label(repo, "HDM")["PPM 2.2"].target_node_id

    assert client.post(f"/api/documents/{ppm['id']}/ingest").status_code == 200
    assert len(repo.nodes) == count  # replaced, not doubled
    new = refs_by_label(repo, "HDM")["PPM 2.2"]
    assert new.status == "resolved" and new.target_node_id and new.target_node_id != old_target
    assert new.target_node_id in repo.nodes


def test_a_file_without_headings_fails_with_a_reason_and_can_be_retried(client, repo):
    bad = upload(client, "CADD", make_pdf([[("b", "No headings here.")]]), "cadd.pdf").json()
    res = client.post(f"/api/documents/{bad['id']}/ingest")
    assert res.status_code == 422 and "No headings found" in res.json()["error"]

    saved = next(d for d in client.get("/api/documents").json() if d["id"] == bad["id"])
    assert saved["status"] == "failed" and "No headings found" in saved["error"]

    # fix the stored file, run ingest again
    repo.files[repo.documents[bad["id"]].storage_path] = make_pdf([[("h", "2 Drafting Standards"), ("b", "Text.")]])
    ok = client.post(f"/api/documents/{bad['id']}/ingest")
    assert ok.status_code == 200 and ok.json()["status"] == "ingested" and ok.json()["error"] is None


def test_ingest_unknown_document(client):
    assert client.post("/api/documents/nope/ingest").status_code == 404


def test_delete_removes_nodes_and_file_and_sends_other_refs_back_to_waiting(client, repo, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    cadd = upload_and_ingest(client, "CADD", pdfs["CADD"])
    assert refs_by_label(repo, "PPM")["CADD 2.6"].status == "resolved"

    assert client.delete(f"/api/documents/{cadd['id']}").status_code == 204
    assert not any(n.manual == "CADD" for n in repo.nodes.values())
    assert len(repo.files) == 1
    ref = refs_by_label(repo, "PPM")["CADD 2.6"]
    assert (ref.status, ref.target_node_id) == ("awaiting_cross", None)

    assert client.delete(f"/api/documents/{cadd['id']}").status_code == 404
    assert client.delete(f"/api/documents/{ppm['id']}").status_code == 204
    assert repo.nodes == {} and repo.refs == {} and repo.files == {}
