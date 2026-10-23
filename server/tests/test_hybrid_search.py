"""Search that does not depend on how the question is worded: meaning + keywords + heading, merged."""

import pytest

from app.config import Settings
from app.models import Node
from app.repository.memory import MemoryRepository
from chat_service import answer_question
from retrieval import _fuse, is_relevant, search

from .test_chat import FakeLLM


class Query:
    """Every question embeds to the same vector, so the meaning-search order is fixed by the stored vectors."""

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def node(i, section, title, text, emb, ordinal=0):
    return Node(id=i, document_id="d", manual="PPM", section=section, title=title, page_start=1, page_end=1, ordinal=ordinal, text=text, embedding=emb)


@pytest.fixture
def repo():
    r = MemoryRepository()
    far = [0.0, 1.0, 0.0]
    r.insert_nodes(
        # 20 sections close to the question in meaning: more than the candidates taken from the meaning search
        [node(f"n{i}", f"9.{i}", f"9.{i} Unrelated topic {i}", f"Plain text about topic {i}.", [1.0, 0.1 + 0.01 * i, 0.0], i) for i in range(20)]
        + [
            node("t", "1-1.8", "1-1.8   Content and Development of Electronic Files", "The Master Topographic File contains mapping of existing conditions.", far, 20),
            node("u", "2-2.8", "2-2.8 Erosion Control", "The Master Topographic file (bb file) is maintained by Surveys.", far, 21),
        ]
    )
    return r


def test_a_named_phrase_is_found_even_when_the_meaning_search_ranks_it_last(repo):
    hits = search(repo, Query(), "I want to know where the Master Topographic File is described", k=5)
    found = {h.node.id: h for h in hits}
    assert {"t", "u"} <= set(found)
    assert found["t"].matched_by == ["keywords"] and found["t"].score is None
    assert found["t"].keyword_rank is not None and found["t"].keyword_rank > 0


def test_a_heading_or_section_number_typed_in_full_comes_first(repo):
    hits = search(repo, Query(), "§1-1.8 Content and Development of Electronic Files", k=3)
    assert hits[0].node.id == "t" and "heading" in hits[0].matched_by and hits[0].heading_score == 1.0


def test_a_bare_section_number_or_a_part_of_a_heading_finds_it(repo):
    assert search(repo, Query(), "2-2.8", k=3)[0].node.id == "u"
    assert search(repo, Query(), "content and development", k=3)[0].node.id == "t"


def test_an_ordinary_question_does_not_match_a_heading_by_accident(repo):
    hits = search(repo, Query(), "show me the details of erosion", k=5)
    assert all("heading" not in h.matched_by for h in hits)


def test_fusion_orders_by_agreement_and_pins_exact_headings():
    ranked = _fuse({"meaning": [("a", 0.9), ("b", 0.8)], "keywords": [("b", 1.2), ("c", 1.0)], "heading": []})
    assert [i for i, _ in ranked][0] == "b"  # in two lists
    pinned = _fuse({"meaning": [("a", 0.9)], "keywords": [("a", 1.0)], "heading": [("z", 1.0)]})
    assert pinned[0][0] == "z"  # an exact heading beats agreement of the others


def test_relevance_rule():
    n = node("x", "1", "T", "t", None)
    from retrieval import Hit

    assert is_relevant(Hit(n, 0.5, []), 0.2)
    assert not is_relevant(Hit(n, 0.1, [], ["meaning"]), 0.2)  # far in meaning and nothing else
    assert not is_relevant(Hit(n, 0.1, [], ["meaning", "keywords"], keyword_rank=0.2), 0.2)  # a weak word match
    assert is_relevant(Hit(n, None, [], ["keywords"], keyword_rank=0.9), 0.2)  # its text has the words
    assert is_relevant(Hit(n, None, [], ["keywords"], keyword_rank=1.4), 0.2)  # the exact phrase
    assert is_relevant(Hit(n, None, [], ["heading"], heading_score=1.0), 0.2)


def test_chat_answers_from_a_section_found_only_by_its_exact_name(repo):
    llm = FakeLLM("It is described in [S1] and [S2].")
    res = answer_question(repo, Query(), llm, Settings(chat_top_k=3, chat_locate_top_k=3, chat_min_score=0.999), "Where is the Master Topographic File described?")
    # The sections close in meaning are filtered out (cosine below the limit); the ones with the words stay.
    assert res.covered and "1-1.8" in {s.section for s in res.sources}
    assert all("keywords" in s.matched_by for s in res.sources)
    assert any(s.matched_by == ["keywords"] for s in res.sources)


def test_missing_sql_functions_fall_back_to_meaning_only():
    from postgrest.exceptions import APIError

    from app.repository.supabase_repo import SupabaseRepository

    class Rpc:
        def execute(self):
            raise APIError({"message": "Could not find the function", "code": "PGRST202"})

    class Client:
        def rpc(self, name, params):
            return Rpc()

    repo = SupabaseRepository.__new__(SupabaseRepository)
    repo.client = Client()
    assert repo.match_nodes_text("x", 5) == [] and repo.match_nodes_title("x", 5) == []


def test_a_where_question_searches_wider_and_asks_for_every_place(repo):
    from chat_service import LOCATE_NOTE, asks_where

    assert asks_where("I want to know the article where I got some info of Master Topographic File")
    assert asks_where("Which section covers erosion control?")
    assert not asks_where("How wide is a lane?")

    llm = FakeLLM("It appears in [S1] and [S2].")
    answer_question(repo, Query(), llm, Settings(chat_top_k=2, chat_locate_top_k=6, chat_min_score=0.0), "Which section mentions the Master Topographic File?")
    prompt = llm.calls[0][1][-1]["content"]
    assert LOCATE_NOTE in prompt
    assert prompt.count("---") >= 3  # more than the 2 sections of the normal limit were sent
    llm2 = FakeLLM("Answer [S1].")
    answer_question(repo, Query(), llm2, Settings(chat_top_k=2, chat_min_score=0.0), "How is the Master Topographic File made?")
    assert LOCATE_NOTE not in llm2.calls[0][1][-1]["content"]


def test_filler_words_do_not_match_sections(repo):
    from app.models import Node

    repo.insert_nodes([node("f", "3.1", "3.1 Policies", "Tell me what the article says: the information you want to know.", [0.0, 0.0, 1.0], 30)])
    hits = repo.match_nodes_text("I want to know the article where I got some info of Master Topographic File", 10)
    assert "f" not in {i for i, _ in hits} and {"t", "u"} <= {i for i, _ in hits}
