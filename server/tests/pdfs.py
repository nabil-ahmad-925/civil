"""Builds small manual-like PDFs for tests: bold, larger headings; body text; running header and page number."""

import pymupdf

HEADING = ("h", 14)
BODY = ("b", 10)

PPM_PAGES = [
    [
        ("h", "2 Project Plans"),
        ("b", "Project plans consist of the sheets described below."),
        ("h", "2.1 General"),
        ("b", "General information applies to every sheet."),
        ("h", "2.1.3 Title Block"),
        ("b", "The title block shall show the district and county."),
    ],
    [
        ("h", "2.2 Layout Sheets"),
        ("b", "Layout sheets show the alignment. See Section 2.1.3 for the title block."),
        ("b", "Line weights follow Section 2.6 of the CADD Users Manual."),
        ("b", "Levels are listed in Table 2.4A."),
    ],
    [
        ("h", "2.3 Notes"),
        ("b", "Every sheet carries general notes."),
    ],
]

HDM_PAGES = [
    [
        ("h", "Chapter 300 Geometric Cross Section"),
        ("b", "This chapter covers cross sections."),
        ("h", "Index 301.1 Lane Width"),
        ("b", "The minimum lane width shall be 12 feet. See Index 301.5 for shoulders."),
        ("b", "Drawings follow Section 2.2 of the Plans Preparation Manual."),
    ],
    [
        ("h", "Index 301.5 Shoulder Width"),
        ("b", "Shoulders are as shown in the table. See Section 9.9 of the CADD Users Manual."),
        ("b", "Compare with HDM Index 301.1 above."),
    ],
]

CADD_PAGES = [
    [
        ("h", "2 Drafting Standards"),
        ("b", "Drafting rules for plan sheets."),
        ("h", "2.6 Line Weights"),
        ("b", "Line weights are assigned by level."),
    ],
    [
        ("b", "Text height is set per level."),
    ],
]


def make_pdf(pages: list[list[tuple[str, str]]], running_header: str = "Caltrans Manual 2024") -> bytes:
    doc = pymupdf.open()
    for number, items in enumerate(pages, start=1):
        page = doc.new_page()
        page.insert_text((50, 30), running_header, fontsize=8, fontname="helv")
        y = 80
        for kind, text in items:
            if kind == "h":
                page.insert_text((50, y), text, fontsize=14, fontname="hebo")
                y += 26
            else:
                page.insert_text((50, y), text, fontsize=10, fontname="helv")
                y += 18
        page.insert_text((280, 820), f"Page {number}", fontsize=8, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data
