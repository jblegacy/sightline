from sightline.voice import VOICE_RULES, voice_reference


def test_voice_rules_bans_em_and_en_dash():
    assert "em dash" in VOICE_RULES
    assert "en dash" in VOICE_RULES


def test_voice_rules_lists_banned_words():
    # Matches the 2026-08-17 writing guide's banned list, not the earlier
    # VOICE_PROFILE.md-derived one it superseded — "deeply passionate," not
    # "passionate about," is what the guide actually bans.
    for word in ("leverage", "spearheaded", "synergy", "deeply passionate", "intersection"):
        assert word in VOICE_RULES


def test_voice_reference_picks_longest_usable_answers():
    answers = [
        {"question_type": "short", "status": "ready", "text": "short one"},
        {"question_type": "draft_long", "status": "draft", "text": "d" * 300},
        {"question_type": "real_long", "status": "ready", "text": "r" * 250},
        {"question_type": "real_longer", "status": "verified", "text": "v" * 400},
    ]
    ref = voice_reference(answers, n=2)
    assert "v" * 400 in ref
    assert "r" * 250 in ref
    assert "d" * 300 not in ref  # draft status excluded even though long
    assert "short one" not in ref  # under the 200-char floor


def test_voice_reference_empty_when_nothing_usable():
    assert voice_reference([]) == "(none available)"
