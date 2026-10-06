"""Validation for the temporary guest review. Saved history stays opaque."""
from collections import Counter
from fastapi import HTTPException
from backend.documents import LABELS


def validate(value, original):
    try:
        assert isinstance(value, dict) and value['v'] == 1
        text = value['text']
        assert isinstance(text, str) and text == original and len(text) <= 2_000_000
        assert value['mode'] in {'redact', 'placeholder', 'synthetic'}
        assert type(value['revision']) is int and value['revision'] >= 0
        details = value['details']
        assert isinstance(details, list) and len(details) <= 200_000
        offsets = {0: 0}; units = 0
        for i, char in enumerate(text):
            units += 2 if ord(char) > 0xffff else 1
            offsets[units] = i + 1
        cursor, pieces, counts, labels = 0, [], Counter(), set()
        for d in details:
            assert type(d['start']) is int and type(d['end']) is int
            start, end = offsets[d['start']], offsets[d['end']]
            assert cursor <= start < end <= len(text)
            assert d['category'] in {*LABELS, 'manual'}
            assert isinstance(d['label'], str) and 0 < len(d['label']) <= 128
            assert isinstance(d['replacement'], str) and 0 < len(d['replacement']) <= 128
            pieces.extend([text[cursor:start], d['replacement']]); cursor = end
            labels.add(d['label']); counts[d['category']] += 1
        pieces.append(text[cursor:])
        assert value['redacted'] == ''.join(pieces)
        assert type(value['detailCount']) is int and value['detailCount'] == len(labels)
        return dict(counts)
    except (AssertionError, KeyError, TypeError, ValueError):
        raise HTTPException(422, 'Invalid review text or overlapping spans.') from None
