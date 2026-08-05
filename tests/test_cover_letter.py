from unittest.mock import MagicMock

from docx import Document as DocxDocument

from sightline.cover_letter import build_user_content, generate_cover_letter, render_cover_letter_docx

BULLETS = [
    {"ref": "BL-001", "text": "Built the automation platform.", "status": "verified"},
    {"ref": "BL-002", "text": "Scaled revenue from $250K to $12M.", "status": "verified"},
    {"ref": "BL-003", "text": "Unverified claim.", "status": "draft"},
]

POSTING = {
    "title": "Program Operations Manager",
    "companies": {"name": "Convergent Research"},
    "jd_text": "Own applicant pipeline operations end to end.",
}

SCORE = {
    "rationale": "Strong overlap.",
    "keywords": ["applicant pipeline"],
    "unmet_requirements": ["startup ops background"],
    "company_signals": ["nonprofit research studio"],
}


def test_build_user_content_includes_jd_and_score_context():
    content = build_user_content(POSTING, SCORE)
    assert "Program Operations Manager" in content
    assert "Convergent Research" in content
    assert "Own applicant pipeline operations end to end." in content
    assert "Strong overlap." in content


def test_generate_cover_letter_only_grounds_in_verified_bullets():
    fake_client = MagicMock()
    fake_client.chat_call.return_value = ("Paragraph one.\n\nParagraph two.", 0.015)
    text, cost = generate_cover_letter(fake_client, POSTING, SCORE, BULLETS, ["BL-001"])

    assert text == "Paragraph one.\n\nParagraph two."
    assert cost == 0.015
    sent_system = fake_client.chat_call.call_args.kwargs["system"]
    assert "Built the automation platform." in sent_system
    assert "Scaled revenue from $250K to $12M." in sent_system
    assert "Unverified claim." not in sent_system


def test_generate_cover_letter_separates_selected_from_other_bullets():
    fake_client = MagicMock()
    fake_client.chat_call.return_value = ("x", 0.01)
    generate_cover_letter(fake_client, POSTING, SCORE, BULLETS, ["BL-001"])
    sent_system = fake_client.chat_call.call_args.kwargs["system"]
    # BL-001 (selected) should appear before the "rest of the library" section
    selected_idx = sent_system.index("Built the automation platform.")
    other_idx = sent_system.index("Scaled revenue from $250K to $12M.")
    assert selected_idx < other_idx


def test_render_cover_letter_docx_produces_valid_docx_with_body_text():
    docx_bytes = render_cover_letter_docx(
        "First paragraph of the letter.\n\nSecond paragraph.", "Convergent Research", "Program Operations Manager",
    )
    assert docx_bytes[:2] == b"PK"  # docx is a zip archive
    import io
    doc = DocxDocument(io.BytesIO(docx_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "First paragraph of the letter." in full_text
    assert "Second paragraph." in full_text
    assert "Convergent Research" in full_text
    assert "Program Operations Manager" in full_text
