"""Bounded extraction of text, PDF and public web content."""
from html.parser import HTMLParser
from io import BytesIO
import re

from app.domain.research import EvidenceLocator, ExtractedDocument, TextUnit

MAX_RAW_BYTES = 8 * 1024**2


def validate_raw(raw: bytes):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RAW_BYTES:
        raise ValueError('Provide between 1 byte and 8 MiB of source bytes')


class _Paragraphs(HTMLParser):
    blocks = {'p', 'div', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'tr'}
    hidden = {'head', 'script', 'style', 'noscript', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.buffer = []
        self.units = []
        self.section = None
        self.anchor = None
        self.heading = False

    def flush(self):
        text = re.sub(r'\s+', ' ', ''.join(self.buffer)).strip()
        if text:
            if self.heading:
                self.section = text
            self.units.append(TextUnit(text=text, locator=EvidenceLocator(kind='web_paragraph', paragraph=len(self.units) + 1, section=self.section, anchor=self.anchor)))
        self.buffer = []
        self.anchor = None
        self.heading = False

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.skip += 1
        if self.skip:
            return
        if tag in self.blocks:
            self.flush()
            self.anchor = dict(attrs).get('id') or None
            self.heading = tag.startswith('h') and tag[1:].isdigit()
        elif tag in ('br', 'td', 'th'):
            self.buffer.append(' ')

    def handle_endtag(self, tag):
        if self.skip:
            if tag in self.hidden:
                self.skip -= 1
            return
        if tag in self.blocks:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data)


def extract_web(raw: bytes, sections=()) -> ExtractedDocument:
    validate_raw(raw)
    parser = _Paragraphs()
    parser.feed(raw.decode('utf-8-sig')); parser.close(); parser.flush()
    units = tuple(unit for unit in parser.units if not sections or unit.locator.section in sections)
    return ExtractedDocument(extractor='html-paragraphs-v1', units=units)


def extract_pdf(raw: bytes) -> ExtractedDocument:
    validate_raw(raw)
    import pypdf
    reader = pypdf.PdfReader(BytesIO(raw))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 50:
        raise ValueError('Use an unencrypted text PDF of at most 50 pages')
    units = []
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ''
        if text.strip():
            units.append(TextUnit(text=text, locator=EvidenceLocator(kind='pdf_page', page=number)))
    return ExtractedDocument(extractor='pypdf-' + pypdf.__version__, units=tuple(units), total_pages=len(reader.pages))


def extract_text(raw: bytes) -> ExtractedDocument:
    validate_raw(raw)
    paragraphs = [text for text in re.split(r'\n\s*\n', raw.decode('utf-8-sig')) if text.strip()]
    return ExtractedDocument(extractor='utf8-paragraphs-v1', units=tuple(
        TextUnit(text=text, locator=EvidenceLocator(kind='text_paragraph', paragraph=i + 1))
        for i, text in enumerate(paragraphs)))


def extract_document(kind, raw, sections=()):
    return extract_web(raw, sections) if kind == 'web' else {'pdf': extract_pdf, 'text': extract_text}[kind](raw)
