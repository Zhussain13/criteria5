"""Formatted SAR text (from the Word-style editor): clean it, turn it into plain text for the audit,
and write it into the Word SAR document with its fonts, sizes, alignment, lists, tables and merged cells.
"""
from __future__ import annotations

import base64
import io
import re
from html.parser import HTMLParser

try:
    import nh3
except ImportError:  # nh3 is in requirements.txt; without it, formatting is dropped (safe, plain text)
    nh3 = None

TAGS = {"p", "br", "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup", "span", "div", "font",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td",
        "th", "colgroup", "col", "caption", "blockquote", "hr", "img", "a", "pre", "code"}
ATTRS = {"*": {"style"}, "td": {"colspan", "rowspan", "width"}, "th": {"colspan", "rowspan", "width"},
         "table": {"border", "width", "cellpadding", "cellspacing"}, "col": {"width"},
         "img": {"src", "alt", "width", "height"}, "a": {"href"}, "ol": {"start"}}
STYLES = {"font-family", "font-size", "font-weight", "font-style", "text-decoration", "color", "background-color",
          "text-align", "vertical-align", "width", "height", "border", "border-collapse", "border-width",
          "border-style", "border-color", "padding", "padding-left", "margin-left", "line-height",
          "text-indent", "list-style-type"}
BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "ul", "ol", "table", "hr"}


def _attr_filter(tag, attr, value):
    if attr == "href":
        return value if re.match(r"(https?:|mailto:)", value, re.I) else None
    if attr == "src":
        return value if re.match(r"(data:image/(png|jpe?g|gif|webp);base64,|https:)", value, re.I) else None
    return value


def sanitize(html: str) -> str:
    """Keep formatting, drop anything that could run code in another user's browser."""
    if not html or not html.strip():
        return ""
    if nh3 is None:
        return _fallback_clean(html)
    return nh3.clean(html, tags=TAGS, attributes=ATTRS, url_schemes={"http", "https", "mailto", "data"},
                     attribute_filter=_attr_filter, filter_style_properties=STYLES, link_rel=None)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in ("br", "tr") or tag in BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def to_text(html: str) -> str:
    """Plain text of the formatted section, for the audit and the AI (tables become 'a | b' lines)."""
    if not html:
        return ""
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\xa0]+", " ", text)).strip()


# ------------------------------------------------------------------ HTML -> simple tree

class _Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs or {}), parent, []


class _Tree(HTMLParser):
    VOID = {"br", "img", "hr", "col"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in self.VOID:
            self.cur = n

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _style(node) -> dict:
    out = {}
    for part in (node.attrs.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _length_pt(v: str) -> float | None:
    m = re.match(r"([\d.]+)\s*(pt|px|em|rem|cm|mm)?", v or "")
    if not m:
        return None
    n, unit = float(m.group(1)), (m.group(2) or "px")
    return {"pt": n, "px": n * 0.75, "em": n * 11, "rem": n * 11, "cm": n * 28.35, "mm": n * 2.835}[unit]


def _color(v: str):
    from docx.shared import RGBColor
    v = (v or "").strip()
    m = re.match(r"#([0-9a-f]{6})$", v, re.I) or re.match(r"#([0-9a-f]{3})$", v, re.I)
    if m:
        h = m.group(1) if len(m.group(1)) == 6 else "".join(c * 2 for c in m.group(1))
        return RGBColor.from_string(h.upper())
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", v)
    return RGBColor(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


SIZES = {"xx-small": 7, "x-small": 7.5, "small": 10, "medium": 12, "large": 13.5, "x-large": 18, "xx-large": 24}
HEADING_PT = {"h1": 16, "h2": 14, "h3": 13, "h4": 12, "h5": 11, "h6": 11}


def _fmt(fmt: dict, node) -> dict:
    f = dict(fmt)
    t = node.tag
    if t in ("b", "strong", "th") or t in HEADING_PT:
        f["bold"] = True
    if t in ("i", "em"):
        f["italic"] = True
    if t == "u":
        f["underline"] = True
    if t in ("s", "strike"):
        f["strike"] = True
    if t == "sub":
        f["sub"] = True
    if t == "sup":
        f["sup"] = True
    if t in ("code", "pre"):
        f["font"] = "Courier New"
    if t in HEADING_PT:
        f["size"] = HEADING_PT[t]
    st = _style(node)
    if st.get("font-weight") in ("bold", "700", "800", "900"):
        f["bold"] = True
    if st.get("font-style") == "italic":
        f["italic"] = True
    if "underline" in st.get("text-decoration", ""):
        f["underline"] = True
    if "line-through" in st.get("text-decoration", ""):
        f["strike"] = True
    if st.get("font-family"):
        f["font"] = st["font-family"].split(",")[0].strip().strip("'\"")
    if st.get("font-size"):
        f["size"] = SIZES.get(st["font-size"]) or _length_pt(st["font-size"]) or f.get("size")
    if st.get("color"):
        f["color"] = st["color"]
    return f


def _run(par, text, f):
    from docx.shared import Pt
    r = par.add_run(text)
    r.bold, r.italic, r.underline = f.get("bold"), f.get("italic"), f.get("underline")
    if f.get("strike"):
        r.font.strike = True
    if f.get("sub"):
        r.font.subscript = True
    if f.get("sup"):
        r.font.superscript = True
    if f.get("font"):
        r.font.name = f["font"]
    if f.get("size"):
        r.font.size = Pt(f["size"])
    if f.get("color") and _color(f["color"]) is not None:
        r.font.color.rgb = _color(f["color"])
    return r


def _para_format(par, node):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    st = _style(node)
    align = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
             "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT}.get(st.get("text-align", ""))
    if align is not None:
        par.alignment = align
    indent = _length_pt(st.get("padding-left") or st.get("margin-left") or "")
    if indent:
        par.paragraph_format.left_indent = Pt(indent)


def _image(par, src, node):
    from docx.shared import Inches
    m = re.match(r"data:image/[a-z]+;base64,(.*)", src or "", re.S)
    if not m:
        return
    try:
        w = _length_pt(_style(node).get("width") or node.attrs.get("width") or "")
        width = Inches(min(6.0, (w or 432) / 72))
        par.add_run().add_picture(io.BytesIO(base64.b64decode(m.group(1))), width=width)
    except Exception:  # a broken picture must not stop the whole SAR document
        par.add_run("[picture could not be added]")


def _has_block(node) -> bool:
    return any(not isinstance(c, str) and (c.tag in BLOCKS or _has_block(c)) for c in node.children)


def _inline(par, node, fmt):
    for c in node.children:
        if isinstance(c, str):
            if c.strip() or c == " ":
                _run(par, re.sub(r"\s+", " ", c), fmt)
        elif c.tag == "br":
            par.add_run().add_break()
        elif c.tag == "img":
            _image(par, c.attrs.get("src"), c)
        else:
            _inline(par, c, _fmt(fmt, c))


def _blocks(container, nodes, fmt, list_style=None):
    par = None
    for c in nodes:
        if isinstance(c, str):
            if not c.strip():
                continue
            par = par or container.add_paragraph()
            _run(par, re.sub(r"\s+", " ", c), fmt)
        elif c.tag in ("ul", "ol"):
            par = None
            _blocks(container, c.children, fmt, "List Number" if c.tag == "ol" else "List Bullet")
        elif c.tag == "table":
            par = None
            _table(container, c, fmt)
        elif c.tag == "hr":
            par = None
            container.add_paragraph("_" * 40)
        elif c.tag in BLOCKS:
            par = None
            f = _fmt(fmt, c)
            if _has_block(c):
                _blocks(container, c.children, f, list_style)
                continue
            p = container.add_paragraph(style=list_style if c.tag == "li" and list_style else None)
            _para_format(p, c)
            _inline(p, c, f)
        elif c.tag in ("br",):
            par = par or container.add_paragraph()
            par.add_run().add_break()
        elif c.tag == "img":
            par = par or container.add_paragraph()
            _image(par, c.attrs.get("src"), c)
        elif c.tag in ("colgroup", "col", "caption"):
            continue
        else:  # inline element at block level (span, b, a...)
            par = par or container.add_paragraph()
            if _has_block(c):
                par = None
                _blocks(container, c.children, _fmt(fmt, c), list_style)
            else:
                _inline(par, c, _fmt(fmt, c))


def _rows(table_node):
    rows = []
    for c in table_node.children:
        if isinstance(c, str):
            continue
        if c.tag == "tr":
            rows.append(c)
        elif c.tag in ("thead", "tbody", "tfoot"):
            rows += [r for r in c.children if not isinstance(r, str) and r.tag == "tr"]
    return rows


def _table(container, node, fmt):
    rows = _rows(node)
    if not rows:
        return
    placed, taken, ncols = [], set(), 0
    for r, tr in enumerate(rows):
        col = 0
        for cell in [c for c in tr.children if not isinstance(c, str) and c.tag in ("td", "th")]:
            while (r, col) in taken:
                col += 1
            cs = max(1, int(cell.attrs.get("colspan") or 1))
            rs = max(1, int(cell.attrs.get("rowspan") or 1))
            rs = min(rs, len(rows) - r)
            for dr in range(rs):
                for dc in range(cs):
                    taken.add((r + dr, col + dc))
            placed.append((r, col, rs, cs, cell))
            col += cs
            ncols = max(ncols, col)
    ncols = max(ncols, max((c for _, c in taken), default=0) + 1)
    t = container.add_table(rows=len(rows), cols=ncols)
    try:
        t.style = "Table Grid"
    except Exception:
        pass
    for r, c, rs, cs, cell in placed:
        target = t.cell(r, c)
        if rs > 1 or cs > 1:
            target = target.merge(t.cell(r + rs - 1, c + cs - 1))
        before = len(target.paragraphs)
        _blocks(target, [cell] if not _has_block(cell) else cell.children, _fmt(fmt, cell))
        if before and len(target.paragraphs) > before and not target.paragraphs[0].text:
            p = target.paragraphs[0]._element
            p.getparent().remove(p)
        if not _has_block(cell):  # the cell itself was rendered as one block: keep its alignment
            for p in target.paragraphs:
                _para_format(p, cell)


def add_html(container, html: str, base: dict | None = None):
    """Write formatted SAR text into a python-docx Document (or table cell)."""
    if not html or not html.strip():
        return
    tree = _Tree()
    tree.feed(html)
    _blocks(container, tree.root.children, dict(base or {}))


def html_to_plain(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


_SAFE_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "table", "thead", "tbody", "tr", "td",
              "th", "h1", "h2", "h3", "h4", "span", "a", "blockquote", "sub", "sup", "hr"}
_SAFE_ATTRS = {"a": {"href"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}, "table": {"border"}}
_VOID = {"br", "hr"}


def _fallback_clean(html: str) -> str:
    """Allow-list cleaner used only when nh3 is not installed: keeps basic formatting and tables,
    drops scripts, styles, event handlers and unknown tags (their text stays, script/style text is dropped)."""
    import html as _h
    from html.parser import HTMLParser

    out: list[str] = []
    skip = [0]

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                skip[0] += 1
                return
            if skip[0] or tag not in _SAFE_TAGS:
                return
            keep = []
            for k, v in attrs:
                if k in _SAFE_ATTRS.get(tag, set()) and v is not None:
                    if k == "href" and not re.match(r"(https?:|mailto:)", v, re.I):
                        continue
                    keep.append(f' {k}="{_h.escape(v, quote=True)}"')
            out.append(f"<{tag}{''.join(keep)}>")

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                skip[0] = max(0, skip[0] - 1)
                return
            if not skip[0] and tag in _SAFE_TAGS and tag not in _VOID:
                out.append(f"</{tag}>")

        def handle_data(self, data):
            if not skip[0]:
                out.append(_h.escape(data, quote=False))

    pr = P(convert_charrefs=True)
    pr.feed(html)
    pr.close()
    return "".join(out)
