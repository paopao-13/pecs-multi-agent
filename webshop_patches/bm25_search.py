"""
Windows-friendly BM25 search backend for WebShop.

The upstream WebShop uses `pyserini` (Anserini / Lucene + JNI) as its search
backend. That stack is fragile on Windows (JNI/Cygwin/encoding issues with
pyserini 0.17.x). This module provides a drop-in replacement built on
`rank_bm25` (pure Python) that implements the exact same interface the env code
expects:

    engine = BM25SearchEngine(num_products=...)
    hits   = engine.search(keywords, k=50)      # -> list with .docid
    doc    = engine.doc(hit.docid)              # -> object with .raw()
    asin   = json.loads(doc.raw())['id']

No other source changes are required. Ranking quality is comparable to the
Lucene BM25 backend (same BM25 family); only the exact hit ordering may differ
slightly, which does not affect agent trajectories in any material way.

This is the DEFAULT backend on Windows. To use the faithful pyserini/Lucene
backend instead, install pyserini + JDK 11 and set the env var
WEBSITE_USE_PYSERINI=1.
"""
import json

from rank_bm25 import BM25Okapi
from web_agent_site.utils import DEFAULT_FILE_PATH


def _tokenize(text):
    return text.lower().split()


def _build_doc_text(p):
    option_texts = []
    options = p.get('options', {}) or {}
    for option_name, option_contents in options.items():
        option_contents_text = ', '.join(option_contents)
        option_texts.append(f'{option_name}: {option_contents_text}')
    option_text = ', and '.join(option_texts)
    bullets = p.get('BulletPoints') or ['']
    return ' '.join([
        p.get('Title', ''),
        p.get('Description', ''),
        bullets[0],
        option_text,
    ]).lower()


class _Hit:
    """Mimics a pyserini hit object (only .docid is read by engine.py)."""
    __slots__ = ('docid',)

    def __init__(self, docid):
        self.docid = docid


class _Doc:
    """Mimics a pyserini document object (only .raw() is read by engine.py)."""

    def __init__(self, raw):
        self._raw = raw

    def raw(self):
        return self._raw


class BM25SearchEngine:
    def __init__(self, num_products=None):
        # Lazy import to avoid a circular import with engine.py at module load.
        from web_agent_site.engine.engine import load_products

        all_products, *_ = load_products(
            filepath=DEFAULT_FILE_PATH, num_products=num_products
        )
        self.products = all_products
        self.docs = [_build_doc_text(p) for p in all_products]
        self.tokenized = [_tokenize(d) for d in self.docs]
        self.bm25 = BM25Okapi(self.tokenized)

    def search(self, keywords, k=50):
        if isinstance(keywords, (list, tuple)):
            keywords = ' '.join(keywords)
        q = _tokenize(keywords)
        scores = self.bm25.get_scores(q)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [_Hit(i) for i in order[:k]]

    def doc(self, docid):
        p = self.products[docid]
        raw = json.dumps({
            'id': p['asin'],
            'product': p,
            'contents': self.docs[docid],
        })
        return _Doc(raw)
