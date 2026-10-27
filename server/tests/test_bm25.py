"""BM25 keyword search: question clean-up, the in-memory ranking, the SQL wiring, and the three searches run together."""

import threading

import pytest

from app.config import Settings
from app.models import Node
from app.repository.keywords import keyword_terms
from app.repository.memory import MemoryRepository
from app.repository.paradedb import ParadeDbKeywordSearch
from app.repository.supabase_repo import SupabaseRepository
from chat_service import answer_question
from retrieval import Hit, is_relevant, search

from .test_chat import FakeLLM


def node(i, title, text, manual="PPM", emb=None, ordinal=0):
    return Node(id=i, document_id="d", manual=manual, section=i, title=title, page_start=1, page_end=1, ordinal=ordinal, text=text, embedding=emb)


def bm25(*nodes):
    repo = MemoryRepository("paradedb")
    repo.insert_nodes(list(nodes))
    return repo


def ids(hits):
    return [i for i, _ in hits]


# --- what the search looks for in a question ----------------------------------------------------------------------


def test_the_filler_of_a_question_is_dropped_and_the_topic_words_stay():
    assert keyword_terms("I need article of dental standards of cleaning") == ["dental", "standards", "cleaning"]
    assert keyword_terms("Tell me what the article says about Master Topographic File, the Master file") == ["says", "master", "topographic", "file"]
    assert keyword_terms("I want to know where") == []
    assert keyword_terms("") == []


def test_numbers_are_split_like_the_index_does_and_single_characters_go():
    assert keyword_terms("Table 856.5 of 1-1.8 a") == ["table", "856"]  # "5", "1" and "8" are single characters
    assert keyword_terms("x marks the spot") == ["marks", "spot"]


# --- the in-memory BM25 (the stand-in for sql/paradedb/001_nodes_bm25.sql) ------------------------------------------------


def test_a_rare_word_outranks_a_common_one():
    repo = bm25(
        node("rare", "Topic", "the survey uses a photogrammetric method"),
        node("a", "Topic", "the survey uses a standard method"),
        node("b", "Topic", "the survey uses a standard method"),
        node("c", "Topic", "the survey uses a standard method"),
    )
    assert ids(repo.match_nodes_text("photogrammetric standard", 5))[0] == "rare"


def test_repeating_a_word_helps_but_a_shorter_section_beats_a_longer_one_with_the_same_count():
    filler = " ".join(f"w{i}" for i in range(200))
    repo = bm25(
        node("short", "T", "drainage ditch"),
        node("long", "T", f"drainage ditch {filler}"),
        node("many", "T", f"drainage drainage drainage ditch {filler}"),
        node("other", "T", "something else entirely"),
    )
    order = ids(repo.match_nodes_text("drainage", 5))
    assert order.index("short") < order.index("long")  # same count, less text around it
    assert order.index("many") < order.index("long")  # more mentions in the same length


def test_a_word_in_the_heading_counts_more_than_the_same_word_in_the_text():
    repo = bm25(
        node("in_text", "Plain heading", "erosion"),
        node("in_title", "Erosion", "plain text"),
        node("none", "Other", "unrelated"),
    )
    assert ids(repo.match_nodes_text("erosion", 5)) == ["in_title", "in_text"]


def test_the_score_is_averaged_over_the_words_of_the_question():
    repo = bm25(node("a", "T", "alpha beta"), node("b", "T", "unrelated"))
    [(_, one)] = repo.match_nodes_text("alpha", 5)
    [(_, with_unknown)] = repo.match_nodes_text("alpha zzzunknown", 5)
    assert with_unknown == pytest.approx(one / 2)


def test_stats_cover_every_manual_and_the_filter_only_selects():
    repo = bm25(
        node("p", "T", "culvert design", manual="PPM"),
        node("h", "T", "culvert design", manual="HDM"),
        node("x", "T", "nothing"),
    )
    everything = dict(repo.match_nodes_text("culvert", 5))
    only_hdm = dict(repo.match_nodes_text("culvert", 5, "HDM"))
    assert set(everything) == {"p", "h"} and set(only_hdm) == {"h"}
    assert only_hdm["h"] == pytest.approx(everything["h"])  # the same weight with or without the filter


def test_the_limit_applies_and_an_all_filler_question_finds_nothing():
    repo = bm25(*[node(f"n{i}", "T", "culvert") for i in range(5)])
    assert len(repo.match_nodes_text("culvert", 3)) == 3
    assert repo.match_nodes_text("I want to know the article", 5) == []


def test_the_default_backend_is_still_the_full_text_stand_in():
    repo = MemoryRepository()
    repo.insert_nodes([node("a", "T", "culvert design")])
    [(_, score)] = repo.match_nodes_text("culvert design", 5)
    assert score == pytest.approx(2.0)  # every word + the exact phrase bonus: the 0..2 scale of sql/004


# --- the wiring: Supabase for the full-text search, a direct connection for ParadeDB ---------------------------------------


class RecordingClient:
    def __init__(self, rows=None, error=None):
        self.calls, self.rows, self.error = [], rows or [], error

    def rpc(self, name, params):
        self.calls.append((name, params))
        client = self

        class Call:
            def execute(self):
                if client.error:
                    raise client.error
                return type("R", (), {"data": client.rows})

        return Call()


class FakeKeywordSearch:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def match(self, query_text, k, manual=None):
        self.calls.append((query_text, k, manual))
        return self.rows


def supabase(backend, client=None, keyword_search=None):
    repo = SupabaseRepository.__new__(SupabaseRepository)
    repo.client = client or RecordingClient()
    repo.keyword_backend = backend
    repo.keyword_search = keyword_search
    return repo


def test_paradedb_gets_the_cleaned_question_and_supabase_is_not_asked():
    search_ = FakeKeywordSearch([("a", 7.5), ("b", 2.0)])
    repo = supabase("paradedb", keyword_search=search_)
    assert repo.match_nodes_text("I need article of dental standards of cleaning", 10, "HDM") == [("a", 7.5), ("b", 2.0)]
    assert search_.calls == [("dental standards cleaning", 10, "HDM")]
    assert repo.client.calls == []


def test_an_all_filler_question_does_not_reach_the_database():
    search_ = FakeKeywordSearch([("a", 1.0)])
    repo = supabase("paradedb", keyword_search=search_)
    assert repo.match_nodes_text("I want to know", 5) == [] and search_.calls == [] and repo.client.calls == []


def test_the_full_text_backend_still_asks_supabase_with_the_question_as_asked():
    repo = supabase("fts", client=RecordingClient(rows=[{"id": "a", "rank": 1.4}]))
    assert repo.match_nodes_text("I need dental standards", 5) == [("a", 1.4)]
    assert repo.client.calls[0][0] == "match_nodes_text" and repo.client.calls[0][1]["query_text"] == "I need dental standards"


def test_the_backend_defaults_to_full_text_so_nothing_changes_until_it_is_switched():
    assert SupabaseRepository.keyword_backend == "fts" and Settings().keyword_backend == "fts"


def test_paradedb_needs_a_connection_string():
    with pytest.raises(RuntimeError, match="PARADEDB_URL"):
        ParadeDbKeywordSearch("")


class FakeConnection:
    def __init__(self, rows=None, fail=None):
        self.rows, self.fail, self.executed, self.closed = rows or [], fail, [], False

    def cursor(self):
        conn = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                if conn.fail:
                    raise conn.fail
                conn.executed.append((sql, params))

            def fetchall(self):
                return conn.rows

        return Cursor()

    def close(self):
        self.closed = True


def test_paradedb_runs_its_function_and_returns_id_and_score():
    conn = FakeConnection(rows=[("a", 7.5), ("b", "2.25")])
    search_ = ParadeDbKeywordSearch("postgresql://x", connect=lambda: conn)
    assert search_.match("dental standards", 10, "HDM") == [("a", 7.5), ("b", 2.25)]
    [(sql, params)] = conn.executed
    assert "match_nodes_bm25(%s::text, %s::int, %s::text)" in sql and params == ("dental standards", 10, "HDM")
    search_.match("again", 5)
    assert len(conn.executed) == 2  # the same connection is kept


def test_a_connection_the_server_closed_is_replaced_once():
    connections = [FakeConnection(fail=OSError("server closed the connection")), FakeConnection(rows=[("a", 1.0)])]
    made = []

    def connect():
        made.append(connections[len(made)])
        return made[-1]

    search_ = ParadeDbKeywordSearch("postgresql://x", connect=connect)
    assert search_.match("culvert", 5) == [("a", 1.0)]
    assert len(made) == 2 and made[0].closed  # the broken one was closed, a fresh one answered


def test_an_unreachable_database_gives_no_keyword_hits_instead_of_an_error(caplog):
    def connect():
        raise OSError("could not connect to server")

    search_ = ParadeDbKeywordSearch("postgresql://user:secret@host/db", connect=connect)
    with caplog.at_level("ERROR"):
        assert search_.match("culvert", 5) == []
    assert "answering without it" in caplog.text and "secret" not in caplog.text  # the password is never logged


# --- relevance and the thresholds ---------------------------------------------------------------------------------


def test_the_keyword_threshold_follows_the_backend():
    assert Settings().keyword_min_score == Settings().keyword_min_score_fts
    assert Settings(keyword_backend="paradedb").keyword_min_score == Settings().keyword_min_score_bm25 > Settings().keyword_min_score_fts


def test_a_bm25_score_is_judged_on_its_own_scale():
    n = node("x", "T", "t")
    weak = Hit(n, None, [], ["keywords"], keyword_rank=2.0)
    strong = Hit(n, None, [], ["keywords"], keyword_rank=9.0)
    assert is_relevant(weak, 0.2)  # 2.0 passes the 0..2 full-text limit of 0.3 ...
    assert not is_relevant(weak, 0.2, Settings(keyword_backend="paradedb").keyword_min_score)  # ... but is weak for BM25
    assert is_relevant(strong, 0.2, Settings(keyword_backend="paradedb").keyword_min_score)


def test_chat_with_bm25_answers_from_a_named_section_and_declines_an_unrelated_question():
    repo = bm25(
        node("t", "1-1.8 Electronic Files", "The Master Topographic File contains mapping of existing conditions.", emb=[0.0, 1.0, 0.0]),
        *[node(f"o{i}", f"Other {i}", f"Plain text about topic {i}.", emb=[0.0, 1.0, 0.0], ordinal=i + 1) for i in range(3)],
    )

    class Q:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    # Nothing is close enough in meaning. BM25 weighs rare words by the size of the corpus, so a 4-section corpus scores far
    # lower than the real one the default limit was measured on: the limit is set for this one.
    settings = Settings(keyword_backend="paradedb", chat_min_score=0.99, keyword_min_score_bm25=0.5)
    found = answer_question(repo, Q(), FakeLLM("It is in [S1]."), settings, "Where is the Master Topographic File described?")
    assert found.covered and found.sources[0].section == "t" and "keywords" in found.sources[0].matched_by
    missing = answer_question(repo, Q(), FakeLLM("never called"), settings, "How do I bake sourdough bread?")
    assert not missing.covered


# --- the three searches run together --------------------------------------------------------------------------------


def test_the_three_searches_run_at_the_same_time():
    barrier = threading.Barrier(3, timeout=5)  # each waits for the other two: sequential calls would time out

    class Repo:
        def match_nodes_text(self, query, k, manual=None):
            barrier.wait()
            return [("a", 5.0)]

        def match_nodes(self, vector, k, manual=None):
            barrier.wait()
            return [("a", 0.9)]

        def match_nodes_title(self, query, k, manual=None):
            barrier.wait()
            return []

        def get_nodes(self, node_ids):
            return [node(i, "T", "t") for i in node_ids]

        def get_node_summaries(self, node_ids):
            return []

    class Q:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    [hit] = search(Repo(), Q(), "anything", k=3, expand=False)
    assert hit.node.id == "a" and sorted(hit.matched_by) == ["keywords", "meaning"]


def test_an_error_in_one_search_is_not_swallowed():
    class Repo:
        def match_nodes_text(self, query, k, manual=None):
            raise RuntimeError("keyword search down")

        def match_nodes(self, vector, k, manual=None):
            return []

        def match_nodes_title(self, query, k, manual=None):
            return []

    class Q:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0]]

    with pytest.raises(RuntimeError, match="keyword search down"):
        search(Repo(), Q(), "anything", k=3)
