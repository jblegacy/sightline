from unittest.mock import MagicMock

from sightline.answers import build_system_prompt, chat_reply, next_ref, slugify_question
from sightline.voice import VOICE_RULES

VERIFIED_BULLET = {
    "ref": "BL-001", "text": "Built the thing.", "status": "verified",
}
DRAFT_BULLET = {
    "ref": "BL-002", "text": "Unverified claim.", "status": "draft",
}
READY_ANSWER = {
    "ref": "A7", "question_type": "tell_me_about_a_failure", "text": "The failure story.", "status": "ready",
}
DRAFT_ANSWER = {
    "ref": "A6", "question_type": "biggest_system_built", "text": "NEEDS INPUT placeholder", "status": "draft",
}


def test_build_system_prompt_includes_only_verified_bullets():
    prompt = build_system_prompt([VERIFIED_BULLET, DRAFT_BULLET], [], None)
    assert "Built the thing." in prompt
    assert "Unverified claim." not in prompt


def test_build_system_prompt_includes_only_ready_or_verified_answers():
    prompt = build_system_prompt([], [READY_ANSWER, DRAFT_ANSWER], None)
    assert "The failure story." in prompt
    assert "NEEDS INPUT placeholder" not in prompt


def test_build_system_prompt_includes_voice_rules():
    # Found live: answers.py never wired VOICE_RULES in at all, so replies
    # used markdown bold and other tells the cover letter prompt already
    # bans — this is the same voice discipline, applied here too.
    prompt = build_system_prompt([], [], None)
    assert VOICE_RULES in prompt
    assert "markdown formatting" in prompt.lower()


def test_build_system_prompt_includes_posting_context_when_given():
    posting = {"title": "Program Operations Manager", "companies": {"name": "Convergent Research"},
               "jd_text": "Own applicant pipeline operations."}
    prompt = build_system_prompt([], [], posting)
    assert "Program Operations Manager" in prompt
    assert "Convergent Research" in prompt
    assert "Own applicant pipeline operations." in prompt


def test_build_system_prompt_omits_posting_context_when_none():
    prompt = build_system_prompt([], [], None)
    assert "CURRENT APPLICATION CONTEXT" not in prompt


def test_chat_reply_sends_grounded_system_prompt():
    fake_client = MagicMock()
    fake_client.chat_call.return_value = ("Here's a draft.", 0.01)
    messages = [{"role": "user", "content": "Draft an answer about X."}]
    reply, cost = chat_reply(fake_client, [VERIFIED_BULLET], [READY_ANSWER], None, messages)
    assert reply == "Here's a draft."
    assert cost == 0.01
    sent_system = fake_client.chat_call.call_args.kwargs["system"]
    assert "Built the thing." in sent_system
    sent_messages = fake_client.chat_call.call_args.kwargs["messages"]
    assert sent_messages == messages


def test_next_ref_extends_existing_a_namespace():
    answers = [{"ref": "A1"}, {"ref": "A9"}, {"ref": "A11"}, {"ref": "B7"}]
    assert next_ref(answers) == "A12"


def test_next_ref_starts_at_a1_when_empty():
    assert next_ref([]) == "A1"


# ---- resemblance surfacing + question_text ----


def test_build_system_prompt_includes_question_text_when_present():
    answer_with_question = {
        "ref": "A7", "question_type": "learning_fast", "status": "ready",
        "text": "The failure story.", "question_text": "Tell me about a time you learned something fast.",
    }
    prompt = build_system_prompt([], [answer_with_question], None)
    assert "Tell me about a time you learned something fast." in prompt


def test_build_system_prompt_instructs_explicit_resemblance_callout():
    prompt = build_system_prompt([], [], None)
    assert "say so explicitly" in prompt.lower()


# ---- slugify_question: deterministic question_type suggestion ----


def test_slugify_question_basic():
    result = slugify_question("Tell me about a time you learned something quickly.")
    assert result == "time_learned_something_quickly"


def test_slugify_question_strips_punctuation_and_stopwords():
    result = slugify_question("How would you help someone understand Generative AI?")
    assert result == "would_help_someone_understand_generative_ai"
    assert "?" not in result


def test_slugify_question_caps_word_count():
    long_q = "Tell me about a time you facilitated a change or adoption journey across a whole department"
    result = slugify_question(long_q, max_words=4)
    assert len(result.split("_")) <= 4


def test_slugify_question_avoids_collision_with_existing_types():
    existing = ["time_learned_something_quickly"]
    result = slugify_question("Tell me about a time you learned something quickly.", existing_types=existing)
    assert result == "time_learned_something_quickly_2"


def test_slugify_question_empty_after_stopword_removal_falls_back():
    assert slugify_question("How do you?") == "question"
