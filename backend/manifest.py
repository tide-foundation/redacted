"""Transient, sensitive detection manifests and their concealed review projection.

This module never writes files. A full manifest contains originals and exact source
offsets: retain it only in working memory until browser-side protection is ready.
Use ``concealed_review`` for default summaries. A separate session-authorized
endpoint may reveal one original explicitly while the guest result is alive.
"""
from collections import Counter
from copy import deepcopy

from backend.documents import DocumentError, Edit, LABELS


SCHEMA_VERSION = 1
MODES = {'redact', 'placeholder', 'synthetic'}
SAFE_WARNINGS = frozenset({
    'Images are preserved but are not scanned for sensitive data.',
    'Original layout unavailable; clean rewrite used.',
    'Input text did not exactly match tokenizer round-trip decode; spans are based on decoded token text.',
})
UNKNOWN_WARNING = 'The processor reported a warning. Review the result before sharing.'
LIMITATIONS = (
    'Only extractable text is scanned. Images are not analysed and no OCR is run.',
    'The model can miss sensitive information or flag ordinary text. Review the output before sharing.',
    'Sensitivity is a detection setting, not a confidence or accuracy score.',
    'Layout preservation applies to the original file format; other download formats are rewritten.',
    'Document properties are removed in every mode. Embedded image metadata is not scanned.',
)


def _safe_warnings(warnings):
    """Never expose arbitrary exception/model text as searchable metadata."""
    if warnings is None:
        return []
    if isinstance(warnings, (str, bytes)):
        raise DocumentError('Processing warnings must be provided separately.')
    result = []
    for warning in warnings:
        if warning is None or warning == '':
            continue
        safe = warning if isinstance(warning, str) and warning in SAFE_WARNINGS else UNKNOWN_WARNING
        if safe not in result:
            result.append(safe)
    return result


def build_manifest(result, edits: list[Edit], *, mode: str, sensitivity: int,
                   source_type: str, layout_preserved: bool, warnings) -> dict:
    """Pair model spans with the exact replacement plan used to export outputs.

    Occurrence numbers are one-based within each category, in extracted-text
    order. Offsets use Python/OPF Unicode code points, not bytes, UTF-16 units,
    PDF page coordinates or Word page numbers. They remain protected alongside
    original values. No confidence is invented: the installed OPF public result
    supplies category and span data, but no calibrated per-detection confidence.

    Callers must not rerun replacement_plan: synthetic replacements can contain
    a fresh nonce and would no longer describe the downloaded output.
    """
    if mode not in MODES:
        raise DocumentError('Unsupported replacement mode.')
    if type(sensitivity) is not int or not 0 <= sensitivity <= 100 or sensitivity % 5:
        raise DocumentError('Unsupported sensitivity setting.')
    if source_type not in {'.pdf', '.docx', 'pdf', 'docx'} or type(layout_preserved) is not bool:
        raise DocumentError('Unsupported scan metadata.')
    # Input parsers use dotted suffixes; API metadata uses the bare file type.
    source_type = source_type.removeprefix('.')
    text = getattr(result, 'text', None)
    if not isinstance(text, str):
        raise DocumentError('The model returned an invalid detection manifest.')
    try:
        spans = list(result.detected_spans)
        for span in spans:
            if (type(span.start) is not int or type(span.end) is not int
                    or not isinstance(span.label, str) or span.label not in LABELS):
                raise ValueError
        spans.sort(key=lambda span: (span.start, span.end))
    except (AttributeError, TypeError, ValueError):
        raise DocumentError('The model returned an invalid detection manifest.') from None
    if len(spans) != len(edits):
        raise DocumentError('Detections do not match the exported replacements.')

    detections, counts, cursor = [], Counter(), 0
    for span, edit in zip(spans, edits):
        if not 0 <= cursor <= span.start < span.end <= len(text):
            raise DocumentError('The model returned unsupported or overlapping spans; no output was saved.')
        if (not isinstance(edit, Edit) or type(edit.start) is not int or type(edit.end) is not int
                or (edit.start, edit.end) != (span.start, span.end) or not isinstance(edit.text, str)):
            raise DocumentError('Detections do not match the exported replacements.')
        original = text[span.start:span.end]
        if getattr(span, 'text', original) != original:
            raise DocumentError('A detected value does not match its source span.')
        counts[span.label] += 1
        detections.append({
            'category': span.label,
            'occurrence': counts[span.label],
            'original': original,
            'replacement': edit.text,
            'replacement_type': mode,
            'span': {
                'start': span.start, 'end': span.end,
                'unit': 'unicode_code_points', 'basis': 'extracted_text',
            },
        })
        cursor = span.end
    return {
        'schema_version': SCHEMA_VERSION,
        'text': text,
        'mode': mode,
        'detections': detections,
        'scan_report': {
            'sensitivity': sensitivity,
            'counts': dict(counts),
            'total_detections': len(detections),
            'source_type': source_type,
            'layout_preserved': layout_preserved,
            'ocr_performed': False,
            'warnings': _safe_warnings(warnings),
            'limitations': list(LIMITATIONS),
        },
    }


def concealed_review(manifest: dict) -> dict:
    """Return category/replacement review without originals or source geometry.

    This is an explicit field projection rather than a shallow copy or a list of
    fields to delete, so added protected manifest fields do not become public.
    The detached result cannot mutate the sensitive in-memory manifest.
    """
    report = manifest['scan_report']
    if manifest.get('correction'):
        counts = Counter()
        detections = []
        for d in manifest['correction']['details']:
            counts[d['category']] += 1
            detections.append({'category': d['category'], 'occurrence': counts[d['category']], 'replacement': d['replacement']})
        return {'detections': detections, 'scan_report': {**deepcopy(report), 'counts': dict(counts),
                'total_detections': len(detections), 'layout_preserved': False,
                'warnings': ['Corrected downloads use a clean text layout.']}}
    return {
        'detections': [{
            'category': item['category'],
            'occurrence': item['occurrence'],
            'replacement': item['replacement'],
        } for item in manifest['detections']],
        'scan_report': deepcopy({key: report[key] for key in (
            'sensitivity', 'counts', 'total_detections', 'source_type',
            'layout_preserved', 'ocr_performed', 'warnings', 'limitations',
        )}),
    }
