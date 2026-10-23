from app.ingestion.resolution import cross_manual_pass, find_section, internal_pass
from app.models import CrossRef, Document, Node
from app.repository.memory import MemoryRepository


def doc(i, manual):
    return Document(id=i, manual=manual, version="v", original_name="x.pdf", size_bytes=1, sha256="s", storage_path="p", status="ingested")


def node(i, document, manual, section, ordinal=0):
    return Node(id=i, document_id=document, manual=manual, section=section, title=f"{section} T", page_start=1, page_end=1, ordinal=ordinal, text="x")


def ref(i, document, mention, manual, section, status="awaiting_cross"):
    return CrossRef(id=i, document_id=document, from_node_id="p1", mention=mention, target_manual=manual, target_section=section, target_label=f"{manual} {section}", status=status)


def test_a_reference_finds_a_section_stored_with_its_kind_word():
    index = {"Topic 103": "a", "103.1": "b", "Chapter 2": "c", "Section 2": "d", "210.2": "e"}
    assert find_section(index, ref("r", "p", "Highway Design Manual Topic 103", "HDM", "103")) == "a"
    assert find_section(index, ref("r", "p", "Section 103.1 of the HDM", "HDM", "103.1")) == "b"  # exact still wins
    # "2" is both a chapter and a section: the word in the mention decides.
    assert find_section(index, ref("r", "p", "Chapter 2 of the manual", "HDM", "2")) == "c"
    assert find_section(index, ref("r", "p", "Section 2 of the manual", "HDM", "2")) == "d"
    assert find_section(index, ref("r", "p", "see 2", "HDM", "2")) in {"c", "d"}  # no word: any, not none
    assert find_section(index, ref("r", "p", "Topic 999", "HDM", "999")) is None
    assert find_section(index, ref("r", "p", "Topic", "HDM", None)) is None


def test_a_paragraph_reference_resolves_to_the_section_that_holds_it():
    index = {"210.2": "e"}
    assert find_section(index, ref("r", "p", "Index 210.2(3)(c)", "HDM", "210.2(3)(c")) == "e"
    assert find_section(index, ref("r", "p", "Index 210.2(3)(c)", "HDM", "210.2(3)(c)")) == "e"


def test_the_cross_manual_pass_links_ppm_to_hdm_topics_once_hdm_is_in():
    repo = MemoryRepository()
    ppm, hdm = doc("d1", "PPM"), doc("d2", "HDM")
    repo.insert_nodes([node("p1", "d1", "PPM", "2-2.3"), node("h1", "d2", "HDM", "Topic 103"), node("h2", "d2", "HDM", "Topic 1102")])
    repo.insert_cross_refs([
        ref("r1", "d1", "Highway Design Manual Topic 103", "HDM", "103"),
        ref("r2", "d1", "Topic 1102 of the Highway Design Manual", "HDM", "1102"),
        ref("r3", "d1", "Topic 999 of the Highway Design Manual", "HDM", "999"),
    ])
    cross_manual_pass(repo, [ppm, hdm])
    got = {r.id: (r.status, r.target_node_id) for r in repo.list_refs("d1")}
    assert got == {"r1": ("resolved", "h1"), "r2": ("resolved", "h2"), "r3": ("unresolved", None)}


def test_the_internal_pass_uses_the_same_lookup():
    repo = MemoryRepository()
    d = doc("d2", "HDM")
    repo.insert_nodes([node("h1", "d2", "HDM", "Topic 103"), node("p0", "d2", "HDM", "210.2")])
    repo.insert_cross_refs([
        ref("r1", "d2", "Topic 103", "HDM", "103", status="unresolved"),
        ref("r2", "d2", "Index 210.2(3)(c)", "HDM", "210.2(3)(c", status="unresolved"),
    ])
    assert internal_pass(repo, d) == {"resolved": 2, "still_unresolved": 0}


def test_the_refresh_endpoint_links_references_again(client, repo):
    ppm, hdm = doc("d1", "PPM"), doc("d2", "HDM")
    repo.insert_document(ppm)
    repo.insert_document(hdm)
    repo.insert_nodes([node("p1", "d1", "PPM", "2-2.3"), node("h1", "d2", "HDM", "Topic 103")])
    repo.insert_cross_refs([ref("r1", "d1", "Highway Design Manual Topic 103", "HDM", "103", status="unresolved")])
    res = client.post("/api/cross-refs/refresh")
    assert res.status_code == 200 and res.json()["cross_manual_pass"]["resolved"] == 1
    assert [(r.status, r.target_node_id) for r in repo.list_refs("d1")] == [("resolved", "h1")]


def test_dot_and_dash_numbering_are_tried_as_a_last_resort():
    index = {"2.6": "cadd", "2-1.6": "ppm"}
    assert find_section(index, ref("r", "p", "Section 2-6 of the CADD Users Manual", "CADD", "2-6")) == "cadd"
    assert find_section(index, ref("r", "p", "Section 2.6 of the CADD Users Manual", "CADD", "2.6")) == "cadd"
    assert find_section(index, ref("r", "p", "Section 2-1.6", "PPM", "2-1.6")) == "ppm"  # exact first
    assert find_section({"2-6": "dash", "2.6": "dot"}, ref("r", "p", "Section 2-6", "CADD", "2-6")) == "dash"  # exact beats a swap

