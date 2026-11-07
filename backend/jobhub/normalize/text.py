"""Text helpers: HTML sanitizing, plain-text extraction, folding, slugs, dates."""
from __future__ import annotations

import html as _html
import re
import unicodedata
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import lxml.html
from lxml import etree

# ---------------------------------------------------------------- folding ---

def fold(text: str | None) -> str:
    """Lowercase, strip accents, collapse whitespace: 'Gabès ' -> 'gabes'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'").replace("œ", "oe").replace("æ", "ae")
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str | None, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")
    return s[:max_len].rstrip("-") or "offre"


def clean_line(text: str | None) -> str | None:
    if text is None:
        return None
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


# ------------------------------------------------------------------- html ---

_ALLOWED_TAGS = {"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h3", "h4", "a",
                 "table", "thead", "tbody", "tr", "td", "th", "blockquote"}
_RENAME = {"h1": "h3", "h2": "h3", "h5": "h4", "h6": "h4", "div": "p", "section": "p", "article": "p"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "input", "button",
                      "select", "textarea", "noscript", "svg", "img", "video", "audio", "head", "title"}
_BLOCK = {"p", "li", "h3", "h4", "tr", "blockquote", "ul", "ol", "table"}


def sanitize_html(raw: str | None) -> str | None:
    """Allow-list sanitizer: keeps structure (lists, headings, emphasis, links)."""
    if not raw or not raw.strip():
        return None
    try:
        root = lxml.html.fragment_fromstring(raw, create_parent="div")
    except (etree.ParserError, ValueError):
        return _html.escape(raw)
    for el in list(root.iter()):
        if el is root or not isinstance(el.tag, str):
            if not isinstance(el.tag, str):  # comments / processing instructions
                el.drop_tree()
            continue
        tag = el.tag.lower()
        if tag in _DROP_WITH_CONTENT:
            el.drop_tree()
            continue
        tag = _RENAME.get(tag, tag)
        el.tag = tag
        if tag not in _ALLOWED_TAGS:
            el.drop_tag()
            continue
        href = el.get("href") if tag == "a" else None
        for attr in list(el.attrib):
            del el.attrib[attr]
        if tag == "a":
            if href and urlsplit(href).scheme in ("http", "https", "mailto"):
                el.set("href", href)
                el.set("rel", "nofollow noopener")
                el.set("target", "_blank")
            else:
                el.drop_tag()
    # Remove empty paragraphs produced by layout markup.
    for el in list(root.iter("p", "li", "strong", "b", "em")):
        if el.getparent() is not None and not el.text_content().strip() and not len(el):
            el.drop_tree()
    out = (root.text or "") + "".join(lxml.html.tostring(c, encoding="unicode") for c in root)
    out = re.sub(r"(<br>\s*){3,}", "<br><br>", out).strip()
    return out or None


def html_to_text(raw: str | None) -> str:
    """Readable plain text with paragraph breaks (for search + snippets)."""
    if not raw:
        return ""
    try:
        root = lxml.html.fragment_fromstring(raw, create_parent="div")
    except (etree.ParserError, ValueError):
        return clean_line(re.sub(r"<[^>]+>", " ", raw)) or ""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == "br":
            el.tail = "\n" + (el.tail or "")
        elif el.tag in _BLOCK or el.tag in ("div", "h1", "h2", "h5", "h6"):
            el.tail = "\n" + (el.tail or "")
            if el.tag == "li":
                el.text = "• " + (el.text or "")
    text = root.text_content()
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text.strip()


def text_to_html(text: str | None) -> str | None:
    if not text:
        return None
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "".join("<p>" + _html.escape(p).replace("\n", "<br>") + "</p>" for p in paras)


def snippet(text: str | None, length: int = 220) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) <= length:
        return t
    cut = t[:length].rsplit(" ", 1)[0]
    return cut + "…"


# ------------------------------------------------------------------ dates ---

_FR_MONTHS = {
    "janvier": 1, "janv": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4, "mai": 5,
    "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_date(value: object) -> datetime | None:
    """Parse ISO 8601, RFC 822, epoch millis and French/English textual dates.

    Returns a naive UTC datetime (the DB stores naive UTC everywhere).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)):
        ts = value / 1000 if value > 10_000_000_000 else value
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        s = str(value).strip()
        dt = None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        if dt is None:
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError, IndexError):
                dt = None
        if dt is None:
            m = re.search(r"(\d{1,2})(?:er)?\s+([a-zA-Zéûèô\.]+)\s+(\d{4})", s)
            if m:
                month = _FR_MONTHS.get(fold(m.group(2)).rstrip("."))
                if month:
                    try:
                        dt = datetime(int(m.group(3)), month, int(m.group(1)))
                    except ValueError:
                        dt = None
        if dt is None:
            m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
            if m:
                try:
                    dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    dt = None
        if dt is None:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if not (2000 <= dt.year <= 2100):
        return None
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
