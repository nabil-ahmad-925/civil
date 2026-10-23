from app.routers.nodes import search_terms

from .conftest import upload_and_ingest


def nodes_of(client, doc, q=""):
    res = client.get("/api/nodes", params={"document_id": doc["id"], "q": q})
    assert res.status_code == 200, res.text
    return res.json()


def by_section(nodes):
    return {n["section"]: n for n in nodes}


def test_list_returns_the_document_in_reading_order_without_text(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    nodes = nodes_of(client, ppm)

    assert [n["section"] for n in nodes] == ["2", "2.1", "2.1.3", "2.2", "2.3"]
    assert [n["ordinal"] for n in nodes] == [0, 1, 2, 3, 4]
    assert all("text" not in n and "embedding" not in n for n in nodes)
    first = nodes[0]
    assert set(first) == {
        "id", "documentId", "manual", "chapter", "section", "title", "parentId",
        "pageStart", "pageEnd", "ordinal", "classification", "depth",
    }
    assert first["manual"] == "PPM" and first["documentId"] == ppm["id"]


def test_depth_follows_the_parent_chain(client, pdfs):
    nodes = by_section(nodes_of(client, upload_and_ingest(client, "PPM", pdfs["PPM"])))
    assert [nodes[s]["depth"] for s in ("2", "2.1", "2.1.3", "2.2")] == [0, 1, 2, 1]
    assert nodes["2.1.3"]["parentId"] == nodes["2.1"]["id"]


def test_each_document_lists_only_its_own_nodes(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    cadd = upload_and_ingest(client, "CADD", pdfs["CADD"])
    assert {n["manual"] for n in nodes_of(client, ppm)} == {"PPM"}
    assert [n["section"] for n in nodes_of(client, cadd)] == ["2", "2.6"]


def test_search_needs_every_word_and_ignores_case(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    assert [n["section"] for n in nodes_of(client, ppm, "title block")] == ["2.1.3", "2.2"]  # its title, and a sentence in 2.2
    assert [n["section"] for n in nodes_of(client, ppm, "DISTRICT county")] == ["2.1.3"]  # text, any case
    assert [n["section"] for n in nodes_of(client, ppm, "alignment")] == ["2.2"]
    assert nodes_of(client, ppm, "title zzzzz") == []                                     # one word missing
    assert len(nodes_of(client, ppm, "   ")) == 5                                         # blank = no filter


def test_a_search_keeps_the_real_depth_of_each_match(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    (hit,) = nodes_of(client, ppm, "district county")
    assert hit["depth"] == 2  # its parents are not in the results, but it is still a level-3 node


def test_search_words_cannot_carry_filter_syntax():
    assert search_terms('lane*, width) or (id.eq.1 "x" 50%_') == ["lane", "width", "or", "id.eq.1", "x", "50"]
    assert len(search_terms("a b c d e f g h")) == 6


def test_unknown_document_and_node(client):
    assert client.get("/api/nodes", params={"document_id": "nope"}).status_code == 404
    assert client.get("/api/nodes").status_code == 422
    assert client.get("/api/nodes/nope").json() == {"error": "Node not found."}


def test_detail_has_the_exact_text_source_and_parent(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    nodes = by_section(nodes_of(client, ppm))
    d = client.get(f"/api/nodes/{nodes['2.2']['id']}").json()

    assert d["text"].splitlines() == [
        "Layout sheets show the alignment. See Section 2.1.3 for the title block.",
        "Line weights follow Section 2.6 of the CADD Users Manual.",
        "Levels are listed in Table 2.4A.",
    ]
    assert d["source"]["originalName"] == "PPM.pdf" and d["source"]["manual"] == "PPM"
    assert len(d["source"]["sha256"]) == 64
    assert d["parent"]["section"] == "2"
    assert "embedding" not in d
    # nothing has tagged this node yet
    assert (d["classification"], d["requirementClass"], d["tags"], d["conditions"]) == (None, None, [], [])


def test_detail_shows_where_each_mention_leads_and_who_mentions_this_node(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    nodes = by_section(nodes_of(client, ppm))
    out = {r["targetLabel"]: r for r in client.get(f"/api/nodes/{nodes['2.2']['id']}").json()["refsOut"]}

    # same manual: linked to a real node
    assert out["PPM 2.1.3"]["status"] == "resolved" and out["PPM 2.1.3"]["resolvedBy"] == "internal_pass"
    assert out["PPM 2.1.3"]["node"]["id"] == nodes["2.1.3"]["id"]
    assert out["PPM 2.1.3"]["mention"] == "See Section 2.1.3"
    # the other manual is not uploaded yet, and a table has no section to look up
    assert out["CADD 2.6"]["status"] == "awaiting_cross" and out["CADD 2.6"]["node"] is None
    assert out["PPM Table 2.4A"]["status"] == "unresolved" and out["PPM Table 2.4A"]["node"] is None

    # the node that was pointed at knows who points at it
    back = client.get(f"/api/nodes/{nodes['2.1.3']['id']}").json()["refsIn"]
    assert [(r["node"]["section"], r["mention"]) for r in back] == [("2.2", "See Section 2.1.3")]


def test_a_reference_into_another_manual_can_be_followed_once_it_is_ingested(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    cadd = upload_and_ingest(client, "CADD", pdfs["CADD"])
    ppm_22 = by_section(nodes_of(client, ppm))["2.2"]
    cadd_26 = by_section(nodes_of(client, cadd))["2.6"]

    ref = next(r for r in client.get(f"/api/nodes/{ppm_22['id']}").json()["refsOut"] if r["targetLabel"] == "CADD 2.6")
    assert ref["status"] == "resolved" and ref["resolvedBy"] == "cross_manual_pass"
    assert ref["node"] == {
        "id": cadd_26["id"], "documentId": cadd["id"], "manual": "CADD", "section": "2.6", "title": "2.6 Line Weights",
    }
    incoming = client.get(f"/api/nodes/{cadd_26['id']}").json()["refsIn"]
    assert [(r["node"]["manual"], r["node"]["section"]) for r in incoming] == [("PPM", "2.2")]


def test_removing_a_document_removes_its_nodes_from_the_api(client, pdfs):
    ppm = upload_and_ingest(client, "PPM", pdfs["PPM"])
    node_id = nodes_of(client, ppm)[0]["id"]
    client.delete(f"/api/documents/{ppm['id']}")
    assert client.get(f"/api/nodes/{node_id}").status_code == 404
    assert client.get("/api/nodes", params={"document_id": ppm["id"]}).status_code == 404
