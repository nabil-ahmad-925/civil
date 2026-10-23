from app.ingestion.pdf_reader import Line, strip_running_lines


def test_a_lone_body_word_repeated_on_several_pages_is_kept():
    lines = [Line(p, "Manual.", 12, False, edge=False) for p in (1, 2, 3)]
    assert strip_running_lines(lines, 3) == lines


def test_margin_lines_repeated_on_several_pages_are_dropped():
    header = [Line(p, "March 2018", 9, False, edge=True) for p in (1, 2, 3)]
    body = Line(1, "Text", 12, False, edge=False)
    assert strip_running_lines([*header, body], 3) == [body]
