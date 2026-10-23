"""Display labels for the existing catalog; never rewrite stored category bytes."""
import json
from pathlib import Path

_LABELS = json.loads((Path(__file__).resolve().parents[2] / 'config' / 'course_categories.json').read_text(encoding='utf-8'))


def category_label(value):
    text = str(value or '').strip()
    if not text:
        return '分类未提供'
    if text in _LABELS:
        return _LABELS[text]
    # These legacy values require a separate data repair review, not guessing
    # a UTF-8/Latin-1 round trip on every response. Preserve the source unchanged.
    if (text.startswith('cat_') or any('\x80' <= c <= '\x9f' for c in text)
            or '\ufffd' in text or text == 'äººå·¥æ™ºèƒ½'):
        return '分类待整理'
    return text


def category_options(rows):
    """One pending-review bucket, rather than many indistinguishable buttons."""
    options = {}
    for row in rows:
        label = category_label(row.get('category'))
        value = '__needs_review__' if label == '分类待整理' else '__uncategorized__' if label == '分类未提供' else row['category']
        item = options.setdefault(value, {'value': value, 'label': label, 'course_count': 0})
        item['course_count'] += int(row['course_count'])
    return list(options.values())
