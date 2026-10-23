from app.ingestion.crossrefs import extract_all, extract_refs
from app.models import Node


class Fake:
    """Returns canned model answers, so no API is called."""

    def __init__(self, *items):
        self.items = list(items)

    def find(self, node):
        return self.items


def item(mention, manual="same", kind="section", number="2.6", document=""):
    return {"mention": mention, "target_manual": manual, "target_document": document, "target_kind": kind, "number": number}


def node(text: str, manual="PPM", section="2.2") -> Node:
    return Node(
        id="n1", document_id="d1", manual=manual, section=section, title="T",
        page_start=1, page_end=1, ordinal=0, text=text,
    )


def run(text, *items, **kw):
    return extract_refs(node(text, **kw), Fake(*items))


def test_other_manual_reference_keeps_the_full_phrase_and_awaits_the_cross_manual_pass():
    text = "Details follow. Refer to Section 2.6 of the CADD Users Manual for line weights."
    [r] = run(text, item("Refer to Section 2.6 of the CADD Users Manual", "CADD"))
    assert (r.target_manual, r.target_section, r.target_label) == ("CADD", "2.6", "CADD 2.6")
    assert r.status == "awaiting_cross"
    assert r.mention == "Refer to Section 2.6 of the CADD Users Manual"


def test_same_manual_reference_starts_unresolved():
    [r] = run("See Index 301.5 for shoulders.", item("See Index 301.5", number="301.5"), manual="HDM", section="301.1")
    assert (r.target_manual, r.target_section, r.status) == ("HDM", "301.5", "unresolved")


def test_tables_have_no_section_to_look_up():
    [r] = run("Levels are listed in Table 2.4A.", item("Table 2.4A", kind="table", number="2.4A"))
    assert r.target_section is None and r.target_label == "PPM Table 2.4A" and r.status == "unresolved"


def test_two_references_in_one_node_give_two_rows():
    text = "See Section 2.1.3 for titles. Refer to Section 2.6 of the CADD for line weights."
    refs = run(text, item("See Section 2.1.3", number="2.1.3"), item("Refer to Section 2.6 of the CADD", "CADD"))
    assert [(r.target_label, r.status) for r in refs] == [("PPM 2.1.3", "unresolved"), ("CADD 2.6", "awaiting_cross")]


def test_one_phrase_pointing_at_two_targets_repeats_the_mention():
    phrase = "Sections 2.1 and 2.3 of the CADD"
    refs = run(f"Refer to {phrase}.", item(phrase, "CADD", number="2.1"), item(phrase, "CADD", number="2.3"))
    assert [r.target_label for r in refs] == ["CADD 2.1", "CADD 2.3"]
    assert {r.mention for r in refs} == {phrase}


def test_a_phrase_that_is_not_in_the_text_is_dropped():
    assert run("The lane width is 12 feet.", item("See Section 9.9", number="9.9")) == []


def test_mention_survives_a_pdf_line_break():
    [r] = run("Please see\nIndex 301.5 for shoulders.", item("Please see Index 301.5", number="301.5"), manual="HDM")
    assert r.mention == "Please see Index 301.5"


def test_a_section_naming_itself_is_ignored():
    assert run("As described in Section 2.2 above.", item("Section 2.2", number="2.2"), section="2.2") == []


def test_repeated_target_is_saved_once_per_node():
    text = "See Section 2.1. Again, Section 2.1 applies."
    assert len(run(text, item("See Section 2.1", number="2.1"), item("Section 2.1", number="2.1"))) == 1


def test_unusable_model_answers_are_dropped():
    text = "See Section 2.1."
    assert run(text, item("See Section 2.1", manual="AASHTO", number="2.1"), item("See Section 2.1", number="")) == []


def test_a_reference_to_another_document_is_kept_as_external():
    doc = "Project Development Procedures Manual"
    [r] = run(f"For details see Chapter 14 of the {doc}.", item(f"Chapter 14 of the {doc}", "other", number="14", document=doc))
    assert (r.target_manual, r.target_label, r.status) == ("OTHER", f"{doc} 14", "external")
    assert r.mention == f"Chapter 14 of the {doc}"


def test_another_document_must_be_named_in_the_text():
    assert run("See Chapter 14 of it.", item("Chapter 14 of it", "other", number="14", document="Made Up Manual")) == []
    assert run("See Chapter 14 of it.", item("Chapter 14 of it", "other", number="14", document="")) == []


def test_text_without_a_number_never_calls_the_model():
    class Boom:
        def find(self, node):
            raise AssertionError("should not be called")

    assert extract_refs(node("The lane width shall be wide."), Boom()) == []


def test_extract_all_keeps_node_order():
    a, b = node("See Section 1.1."), node("See Section 3.3.")
    a.id, b.id = "a", "b"

    class ByNode:
        def find(self, n):
            return [item(f"See Section {n.text[-4:-1]}", number=n.text[-4:-1])]

    refs = extract_all([a, b], ByNode())
    assert [r.from_node_id for r in refs] == ["a", "b"]


def test_a_phrase_without_the_number_it_is_paired_with_is_dropped_and_the_right_one_survives():
    text = "Features refer to the CADD Users Manual. Refer to Section 2.6 of the CADD Users Manual for fonts."
    [r] = run(text, item("refer to the CADD Users Manual", "CADD", number="2.6"), item("Refer to Section 2.6 of the CADD Users Manual", "CADD", number="2.6"))
    assert r.mention == "Refer to Section 2.6 of the CADD Users Manual"


def test_the_number_must_be_a_whole_token_in_the_phrase():
    assert run("See Section 12.6 for fonts.", item("See Section 12.6", number="2.6")) == []


def test_repeated_target_keeps_the_fuller_wording():
    text = "Section 2.6 of the CADD applies. Refer to Section 2.6 of the CADD Users Manual."
    [r] = run(text, item("Section 2.6 of the CADD", "CADD"), item("Refer to Section 2.6 of the CADD Users Manual", "CADD"))
    assert r.mention == "Refer to Section 2.6 of the CADD Users Manual"


def test_the_cross_manual_pass_leaves_external_references_alone():
    from app.ingestion.resolution import cross_manual_pass
    from app.models import Document
    from app.repository.memory import MemoryRepository

    repo = MemoryRepository()
    doc = Document(id="d1", manual="PPM", version="1", original_name="a.pdf", size_bytes=1, sha256="x", storage_path="p", status="ingested")
    repo.insert_document(doc)
    n = node("See Chapter 14 of the Project Development Procedures Manual.")
    repo.insert_nodes([n])
    [ref] = extract_refs(n, Fake(item("Chapter 14 of the Project Development Procedures Manual", "other", number="14", document="Project Development Procedures Manual")))
    repo.insert_cross_refs([ref])
    cross_manual_pass(repo, [doc])
    assert repo.list_refs("d1")[0].status == "external"


def test_short_text_is_one_piece_and_long_text_overlaps_without_losing_anything():
    from app.ingestion.crossrefs import split_text

    assert split_text("short text") == ["short text"]
    words = [f"w{i}" for i in range(600)]
    text = " ".join(words)
    pieces = split_text(text, size=300, overlap=60)
    assert len(pieces) > 1 and all(len(p) <= 300 for p in pieces)
    assert set(" ".join(pieces).split()) == set(words)  # every word is in some piece
    assert pieces[0].split()[-1] in pieces[1].split()  # neighbours share words at the boundary


def test_the_extractor_asks_once_per_piece():
    import json

    from app.ingestion.crossrefs import OpenAIRefExtractor

    class Stub(OpenAIRefExtractor):
        def __init__(self):
            self.model = "gpt-4.1-mini"
            self.calls = []

        def _post(self, body):
            text = body["messages"][1]["content"]
            self.calls.append(text)
            refs = [item("Section 4.1 of the CADD Users Manual", "CADD", number="4.1")] if "Section 4.1" in text else []
            return {"choices": [{"message": {"content": json.dumps({"refs": refs})}}]}

    long = "filler words here. " * 200 + "As defined in Section 4.1 of the CADD Users Manual. " + "more filler. " * 200
    stub = Stub()
    [r] = extract_refs(node(long), stub)
    assert len(stub.calls) > 2 and r.target_label == "CADD 4.1"
