"""Unlinked, grouped free-text suggestions for restricted administrator exports.

Never export respondent IDs, receipts, dates, demographics or questionnaire scores
alongside a suggestion. Automatic masking is a precaution, not a guarantee that
free text contains no indirect identifying details.
"""
from collections import Counter
import re
import unicodedata


EXPORT_NOTICE = (
    'Одинаковые обезличенные предложения объединены, порядок алфавитный. '
    'ФИО, логины, отделения, категории, даты отправки, номера анкет и баллы '
    'не добавляются к текстам. Контакты, ссылки, длинные номера и типовые '
    'форматы ФИО скрываются автоматически. Свободный текст может содержать '
    'косвенные сведения об авторе: перед распространением проверьте содержание.'
)
CAPITAL = 'A-ZА-ЯЁӘҒҚҢӨҰҮҺІ'
LOWER = 'a-zа-яёәғқңөұүһі'
WORD = rf'[{CAPITAL}][{LOWER}]+(?:-[{CAPITAL}][{LOWER}]+)?'
INITIAL = rf'[{CAPITAL}]\.'
NAMES = re.compile(
    rf'(?<!\w)(?:{WORD}\s+{INITIAL}\s*(?:{INITIAL})?|'
    rf'{INITIAL}\s*{INITIAL}\s*{WORD}|'
    rf'{WORD}(?:\s+{WORD}){{1,2}})(?!\w)'
)
EMAIL = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.I)
LINK = re.compile(r'(?:https?://|www\.)[^\s<>]+', re.I)
PHONE_OR_ID = re.compile(r'(?<!\w)\+?\d(?:[\d ()-]{5,}\d)(?!\w)')


def mask_comment(value):
    value = unicodedata.normalize('NFKC', value or '')[:2000]
    value = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)
    value = EMAIL.sub('[контакт скрыт]', value)
    value = LINK.sub('[ссылка скрыта]', value)
    value = PHONE_OR_ID.sub(
        lambda match: '[номер скрыт]' if sum(c.isdigit() for c in match.group()) >= 7 else match.group(),
        value,
    )
    value = NAMES.sub('[имя скрыто]', value)
    return re.sub(r'\s+', ' ', value).strip()


def grouped_comments(responses):
    counts, examples = Counter(), {}
    for response in responses:
        text = mask_comment(response.comment)
        if not text:
            continue
        key = text.casefold()
        counts[key] += 1
        examples[key] = min(examples.get(key, text), text)
    return [{'text': examples[key], 'count': counts[key]} for key in sorted(counts)]
