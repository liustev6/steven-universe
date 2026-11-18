"""Text normalization before the TTS (app/work/normalize.py; spike 13): what each language's numbers
become, what's left alone, and that it never stops a sentence."""

import pytest

from app.work.normalize import TextNormalizer


@pytest.fixture(scope="module")
def normalize():
    return TextNormalizer()


@pytest.mark.parametrize("text, spoken", [
    # Chinese: years digit by digit, amounts and percentages as numbers, phone numbers digit by digit
    ("今天是2026年11月17日。", "今天是二零二六年十一月十七日。"),
    ("这个耳机打折后是299元，便宜了15%。", "这个耳机打折后是两百九十九元，便宜了百分之十五。"),
    ("我的电话号码是138-0013-8000。", "我的电话号码是一三八零零一三八零零零。"),
    ("现在气温是-5.5°C，明天会升到3°C。", "现在气温是负五点五摄氏度，明天会升到三摄氏度。"),
    ("我们9:30在3楼开会，大概1.5小时。", "我们九点三十分在三楼开会，大概一点五小时。"),
    # English
    ("Mount Logan is 5,959 metres tall, or 19,551 feet.",
     "Mount Logan is five thousand nine hundred and fifty nine metres tall, or nineteen thousand five hundred and fifty one feet."),
    ("The meeting is at 3:30 PM on March 5th, 2026.",
     "The meeting is at three thirty PM on the fifth of march, twenty twenty six."),
    # Japanese, Korean
    ("今日は2026年11月17日です。", "今日は二千二十六年十一月十七日です。"),
    ("가격은 15,000원입니다.", "가격은 만오천 원입니다."),
])
def test_numbers_become_words_in_the_sentences_language(normalize, text, spoken):
    assert normalize(text) == spoken


@pytest.mark.parametrize("text", [
    "英国的首都用英文说是London。",            # English words inside Chinese: the model speaks them
    "Hello, how are you?",
    "好的。",
])
def test_words_are_left_alone(normalize, text):
    assert normalize(text) == text


def test_english_words_stay_while_the_number_is_read_in_chinese(normalize):
    assert normalize("我昨天用iPhone 17给Steven发了一条message。") == "我昨天用iPhone 十七给Steven发了一条message。"


def test_a_language_without_a_grammar_passes_through(normalize):
    text = "Сегодня 17 ноября 2026 года."           # Russian: not in the image (base-image keeps zh, en, ja, ko)
    assert normalize(text) == text


def test_a_failure_says_the_sentence_as_written(normalize, monkeypatch):
    broken = TextNormalizer()
    monkeypatch.setattr(broken, "_normalizer", lambda lang: (_ for _ in ()).throw(RuntimeError("boom")))
    assert broken("It costs 15 dollars.") == "It costs 15 dollars."


def test_off_means_untouched():
    assert TextNormalizer(enabled=False)("It is 2026.") == "It is 2026."


def test_warm_loads_every_grammar(normalize):
    normalize.warm()
    assert set(normalize._normalizers) == {"zh", "en", "ja", "ko"}
