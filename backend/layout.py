"""Native-format edits against exact model-input offsets.

DOCX edits text nodes in their existing runs. PDF edits use character geometry,
apply real redactions, then write replacements within the vacated rectangles.
"""
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
import re

from lxml import etree
import pymupdf

from backend.documents import DocumentError, Edit

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
TEXT_TAGS = {f'{{{W}}}t', f'{{{A}}}t'}
PARAGRAPH_TAGS = {f'{{{W}}}p', f'{{{A}}}p'}
IMAGE_WARNING = 'Images are preserved but are not scanned for sensitive data.'


def validate_text(text):
    if not text.strip():
        raise DocumentError('No readable text was found. Scanned documents need OCR first.')
    if len(text) > 200_000:
        raise DocumentError('Extracted text exceeds 200,000 characters. Split the document first.')


def parse_xml(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True, remove_pis=True)
    root = etree.fromstring(data, parser=parser)
    if root.getroottree().docinfo.doctype:
        raise DocumentError('Documents with XML document type declarations are unsupported.')
    return root


@dataclass
class TextNode:
    start: int
    end: int
    element: object


class WordDocument:
    def __init__(self, data):
        self.warning = None
        try:
            with ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 10_000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
                    raise DocumentError('The expanded Word document exceeds the supported size.')
                if len({i.filename for i in infos}) != len(infos):
                    raise DocumentError('This DOCX contains duplicate package entries.')
                if 'word/document.xml' not in archive.namelist():
                    raise DocumentError('This is not a valid DOCX document.')
                self.files = {i.filename: archive.read(i) for i in infos}
        except BadZipFile:
            raise DocumentError('This is not a valid DOCX document.') from None
        if any(name.startswith(('word/embeddings/', 'word/charts/', 'word/diagrams/'))
               or 'vbaProject' in name for name in self.files):
            raise DocumentError('Embedded objects, charts, SmartArt and macros are unsupported. Remove them before uploading.')
        self.roots = {}
        removed = {name for name in self.files if name.startswith(('customXml/', 'docProps/'))
                   or re.match(r'word/(comments[^/]*|people)\.xml$', name)}
        removed_types = ('comments', 'commentsextended', 'commentsextensible', 'commentsids',
                         'people', 'customxml', 'customxmlprops', 'thumbnail',
                         'core-properties', 'extended-properties', 'custom-properties')
        for name, payload in self.files.items():
            if name in removed or not name.endswith(('.xml', '.rels')):
                continue
            root = parse_xml(payload)
            self.roots[name] = root
            if name.endswith('.rels'):
                for child in list(root):
                    kind = child.get('Type', '').rsplit('/', 1)[-1].lower()
                    if child.get('TargetMode') == 'External' or kind in removed_types:
                        root.remove(child)
            if name == '[Content_Types].xml':
                for child in list(root):
                    if child.get('PartName', '').lstrip('/') in removed:
                        root.remove(child)
            if not name.startswith('word/') or name.endswith('.rels'):
                continue
            if root.find(f'.//{{{W}}}altChunk') is not None:
                raise DocumentError('Embedded HTML content is unsupported. Convert it to normal Word text first.')
            # Discard historical text and field instructions; keep their displayed results.
            drop_tags = {f'{{{W}}}{tag}' for tag in (
                'del', 'moveFrom', 'delText', 'instrText', 'delInstrText', 'fldChar', 'commentRangeStart',
                'commentRangeEnd', 'commentReference', 'bookmarkStart', 'bookmarkEnd', 'docVars', 'dataBinding',
            )}
            for element in list(root.iter()):
                if not isinstance(element.tag, str):
                    continue
                local = etree.QName(element).localname
                if element.tag in drop_tags or (element.tag.startswith(f'{{{W}}}') and local.endswith('PrChange')):
                    parent = element.getparent()
                    if parent is not None:
                        parent.remove(element)
                    continue
                for key in list(element.attrib):
                    attr = etree.QName(key).localname
                    if attr in {'author', 'initials', 'date'} or (local == 'fldSimple' and attr == 'instr'):
                        del element.attrib[key]
                    if local in {'docPr', 'cNvPr'} and attr in {'descr', 'title', 'name'}:
                        element.attrib[key] = 'Image' if attr == 'name' else ''
                # Hyperlink display text remains, but its external destination is removed.
                if local == 'hyperlink':
                    for key in list(element.attrib):
                        del element.attrib[key]
        for name in removed:
            self.files.pop(name, None)
        self.nodes = []
        parts, offset = [], 0
        text_parts = ['word/document.xml'] + sorted(
            name for name, root in self.roots.items()
            if name != 'word/document.xml' and name.startswith('word/') and name.endswith('.xml')
            and any(element.tag in TEXT_TAGS for element in root.iter())
        )
        for name in text_parts:
            root = self.roots[name]
            for event, element in etree.iterwalk(root, events=('start', 'end')):
                chunk = ''
                if event == 'start' and element.tag in TEXT_TAGS:
                    chunk = element.text or ''
                    if chunk:
                        self.nodes.append(TextNode(offset, offset + len(chunk), element))
                elif event == 'start' and element.tag == f'{{{W}}}tab':
                    chunk = '\t'
                elif (event == 'end' and element.tag in PARAGRAPH_TAGS) or (
                        event == 'start' and element.tag in {f'{{{W}}}br', f'{{{W}}}cr'}):
                    chunk = '\n'
                parts.append(chunk)
                offset += len(chunk)
            parts.append('\n')
            offset += 1
        self.text = ''.join(parts)
        validate_text(self.text)
        if any(name.startswith('word/media/') for name in self.files):
            self.warning = IMAGE_WARNING

    def save(self, path: Path, edits: list[Edit]):
        operations = defaultdict(list)
        for edit in edits:
            hits = [node for node in self.nodes if node.start < edit.end and node.end > edit.start]
            if not hits:
                raise DocumentError('A detected span could not be mapped back to Word text.')
            for index, node in enumerate(hits):
                operations[id(node)].append((max(edit.start - node.start, 0),
                                             min(edit.end - node.start, node.end - node.start),
                                             edit.text if index == 0 else ''))
        for node in self.nodes:
            text = node.element.text or ''
            for start, end, replacement in sorted(operations[id(node)], reverse=True):
                text = text[:start] + replacement + text[end:]
            node.element.text = text
            node.element.set(XML_SPACE, 'preserve')
        with ZipFile(path, 'w', compression=ZIP_DEFLATED) as archive:
            for name, payload in self.files.items():
                if name in self.roots:
                    payload = etree.tostring(self.roots[name], encoding='UTF-8', xml_declaration=True, standalone=True)
                archive.writestr(name, payload)

    def close(self):
        pass


@dataclass
class Glyph:
    start: int
    end: int
    page: int
    line: int
    rect: object
    origin: tuple
    size: float
    flags: int
    color: int
    direction: tuple


def pdf_font(flags):
    family = 'co' if flags & 8 else 'ti' if flags & 4 else 'he'
    bold, italic = bool(flags & 16), bool(flags & 2)
    if not bold and not italic:
        return {'co': 'cour', 'ti': 'tiro', 'he': 'helv'}[family]
    return family + ('bi' if bold and italic else 'bo' if bold else 'it')


class PdfDocument:
    def __init__(self, data):
        self.document = pymupdf.open(stream=data, filetype='pdf')
        self.warning = None
        self.glyphs = []
        try:
            self._prepare()
        except BaseException:
            self.document.close()
            raise

    def _prepare(self):
        doc = self.document
        if doc.is_encrypted:
            raise DocumentError('Password-protected PDFs are not supported.')
        if len(doc) > 250:
            raise DocumentError('Please use a document with at most 250 pages.')
        if any(list(page.widgets() or []) for page in doc):
            raise DocumentError('Interactive PDF forms are unsupported. Flatten the form before uploading.')
        # Strip metadata, attachments, links and scripts. Keep text for detection,
        # including invisible OCR text, whose image pixels must also be redacted.
        doc.scrub(hidden_text=False, redactions=True, redact_images=2)
        # set_metadata({}) only clears standard keys. Drop custom Info keys too.
        doc.xref_set_key(-1, 'Info', 'null')
        doc.set_toc([])
        parts, offset, line_id = [], 0, 0
        for page in doc:
            for annotation in list(page.annots() or []):
                page.delete_annot(annotation)
            raw = page.get_text('rawdict', sort=True, flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
            page_has_text = False
            for block in raw['blocks']:
                for line in block.get('lines', []):
                    line_id += 1
                    for span in line['spans']:
                        for char in span['chars']:
                            chunk = char['c']
                            self.glyphs.append(Glyph(offset, offset + len(chunk), page.number, line_id,
                                                     pymupdf.Rect(char['bbox']), char['origin'], span['size'],
                                                     span['flags'], span['color'], line['dir']))
                            parts.append(chunk)
                            offset += len(chunk)
                            page_has_text |= bool(chunk.strip())
                    parts.append('\n')
                    offset += 1
                parts.append('\n')
                offset += 1
            if page.get_images():
                self.warning = IMAGE_WARNING
                if not page_has_text:
                    raise DocumentError('This PDF contains a scanned page. Run OCR locally first.')
            parts.append('\n')
            offset += 1
        self.text = ''.join(parts)
        validate_text(self.text)

    def save(self, path: Path, edits: list[Edit]):
        plans = defaultdict(list)
        for edit in edits:
            hits = [g for g in self.glyphs if g.start < edit.end and g.end > edit.start]
            if not hits:
                raise DocumentError('A detected span could not be mapped back to PDF text.')
            groups = defaultdict(list)
            for glyph in hits:
                if abs(glyph.direction[0] - 1) > .001 or abs(glyph.direction[1]) > .001:
                    raise DocumentError('A detected span uses rotated text, which cannot yet be replaced in place.')
                groups[(glyph.page, glyph.line)].append(glyph)
            for index, ((page_number, _), glyphs) in enumerate(groups.items()):
                rect = pymupdf.Rect(glyphs[0].rect)
                for glyph in glyphs[1:]:
                    rect |= glyph.rect
                plans[page_number].append((rect, glyphs[0], edit.text if index == 0 else ''))
        for page_number, replacements in plans.items():
            page = self.document[page_number]
            for rect, _, _ in replacements:
                # Slightly inset the redaction box to avoid touching adjacent glyphs.
                box = pymupdf.Rect(rect.x0 + .05, rect.y0 + .05, rect.x1 - .05, rect.y1 - .05)
                page.add_redact_annot(box, fill=None, cross_out=False)
            page.apply_redactions(images=2, graphics=0, text=0)
            for rect, first, replacement in replacements:
                if not replacement:
                    continue
                fontname = pdf_font(first.flags)
                font = pymupdf.Font(fontname)
                size = min(first.size, rect.width / max(font.text_length(replacement, fontsize=1), .001),
                           rect.height / (font.ascender - font.descender))
                if size < 6:
                    raise DocumentError('A replacement cannot fit legibly in the original PDF space. Try placeholders or upload the Word version.')
                baseline = max(rect.y0 + font.ascender * size,
                               min(first.origin[1], rect.y1 + font.descender * size))
                color = tuple(((first.color >> shift) & 255) / 255 for shift in (16, 8, 0))
                page.insert_text((rect.x0, baseline), replacement, fontsize=size, fontname=fontname, color=color)
        self.document.xref_set_key(-1, 'Info', 'null')
        self.document.del_xml_metadata()
        self.document.save(path, garbage=4, deflate=True, clean=True)

    def close(self):
        self.document.close()


def load_document(data, suffix):
    if suffix == '.docx':
        return WordDocument(data)
    if suffix == '.pdf':
        return PdfDocument(data)
    raise DocumentError('Upload a PDF or DOCX file. Convert legacy .doc files to .docx first.')
