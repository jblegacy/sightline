from sightline.filter import apply_filter


def test_not_remote_archives():
    status, reason = apply_filter({"remote_flag": "false"}, red_flag_phrases=[])
    assert status == "archived"
    assert reason == "not remote"


def test_remote_true_passes_with_no_red_flags():
    status, reason = apply_filter({"remote_flag": "true", "jd_text": "clean posting"}, red_flag_phrases=[])
    assert status == "filtered"
    assert reason is None


def test_unclear_remote_passes_through_not_archived():
    # 'unclear' isn't 'false' — don't archive on ambiguity, only on a clear signal
    status, reason = apply_filter({"remote_flag": "unclear", "jd_text": ""}, red_flag_phrases=[])
    assert status == "filtered"


def test_empty_red_flag_phrases_is_a_noop():
    status, reason = apply_filter(
        {"remote_flag": "true", "jd_text": "must be a licensed real estate agent"}, red_flag_phrases=[]
    )
    assert status == "filtered"


def test_red_flag_phrase_match_archives_case_insensitive():
    status, reason = apply_filter(
        {"remote_flag": "true", "jd_text": "Must hold an Active Real Estate License"},
        red_flag_phrases=["active real estate license"],
    )
    assert status == "archived"
    assert "active real estate license" in reason


def test_red_flag_phrase_no_match_passes():
    status, reason = apply_filter(
        {"remote_flag": "true", "jd_text": "great AI automation role"},
        red_flag_phrases=["real estate license", "commission only"],
    )
    assert status == "filtered"
    assert reason is None
