#!/usr/bin/env python3
"""
epub_to_style.py - build a style sample (my_writing.txt) from an EPUB you own.

Reads chapters in reading order, skips front and back matter, keeps prose
paragraphs of a useful length, and picks passages spread evenly across the
book until the sample reaches the target size. rewrite.py reads the first
6000 characters of the style file, so the default target is a bit under that.

Em dashes are replaced by default (your rewrite gate bans them, and a sample
full of them teaches the model the habit). Use --keep-dashes to keep them.

Works on DRM-free EPUBs only. Stdlib only, Python 3.9+.

    python3 epub_to_style.py book.epub -o my_writing.txt
"""
import argparse
import html
import posixpath
import re
import sys
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

BLOCK_TAGS = {"p", "div", "blockquote", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"}
SKIP_TAGS = {"script", "style", "head", "nav", "sup", "aside"}  # sup/aside: note markers, notes
SKIP_NAME_RE = re.compile(
    r"(cover|title|toc|contents|copyright|dedication|acknowledg|about|also[-_]?by|"
    r"index|colophon|front|back|nav|notes?\b|endnotes?|footnotes?|glossary|praise)", re.I)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def paragraphs(self):
        text = html.unescape("".join(self.parts))
        out = []
        for p in re.split(r"\n\s*\n", text):
            p = re.sub(r"\s+", " ", p).strip()
            if p:
                out.append(p)
        return out


def spine_documents(zf):
    """Chapter file paths in reading order, from the OPF spine."""
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
    opf_path = rootfile.get("full-path")
    opf = ET.fromstring(zf.read(opf_path))
    base = posixpath.dirname(opf_path)

    manifest = {}
    for item in opf.iter():
        if item.tag.endswith("item"):
            manifest[item.get("id")] = (item.get("href"), item.get("media-type", ""),
                                        item.get("properties", ""))
    docs = []
    for ref in opf.iter():
        if ref.tag.endswith("itemref"):
            if ref.get("linear") == "no":
                continue
            href, mtype, props = manifest.get(ref.get("idref"), (None, "", ""))
            if not href or "html" not in mtype or "nav" in props:
                continue
            docs.append(posixpath.normpath(posixpath.join(base, href)))
    return docs


def is_prose(p, min_words, max_words):
    n = len(p.split())
    if not (min_words <= n <= max_words):
        return False
    if p.isupper() or p[-1] not in ".!?\"'”’)…":
        return False                      # headings, captions, fragments
    letters = sum(c.isalpha() for c in p)
    return letters / len(p) > 0.7         # skip tables of numbers, lists of links


def clean(p, keep_dashes):
    if not keep_dashes:
        p = re.sub(r"\s*—\s*(?=[,.;:!?)\"”’])", "", p)   # dash right before punctuation
        p = re.sub(r"\s*—\s*", ", ", p)                    # interruption -> comma
        p = re.sub(r",\s*,", ",", p)
    return p


def build(path, target, min_words, max_words, skip_front, skip_back, keep_dashes):
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        sys.exit("error: not an EPUB (zip) file")
    names = set(zf.namelist())
    # Adobe DRM ships rights.xml, Apple FairPlay ships sinf.xml. encryption.xml
    # alone is often just font obfuscation, so only fail if it covers chapters.
    drm = {"META-INF/rights.xml", "META-INF/sinf.xml"} & names
    if not drm and "META-INF/encryption.xml" in names:
        enc = zf.read("META-INF/encryption.xml").decode("utf-8", "replace")
        drm = re.search(r'URI="[^"]+\.x?html?"', enc)
    if drm:
        sys.exit("error: this EPUB is DRM-protected, so its chapters can't be read. "
                 "Use a DRM-free copy.")
    try:
        docs = spine_documents(zf)
    except (KeyError, StopIteration, ET.ParseError):
        sys.exit("error: could not read the EPUB's table of contents (OPF)")

    docs = [d for d in docs if d in names and not SKIP_NAME_RE.search(posixpath.basename(d))]
    paras = []
    for d in docs:
        ex = TextExtractor()
        ex.feed(zf.read(d).decode("utf-8", "replace"))
        paras += [p for p in ex.paragraphs() if is_prose(p, min_words, max_words)]
    if not paras:
        sys.exit("error: found no prose paragraphs. Try lowering --min-words.")

    # trim front/back matter that survived the filename filter
    a = int(len(paras) * skip_front)
    b = len(paras) - int(len(paras) * skip_back)
    pool = paras[a:b] or paras

    # pick evenly spaced passages until we reach the target size
    picked, size = [], 0
    k = max(1, round(len(pool) / max(1, target // 700)))
    for i in range(0, len(pool), k):
        p = clean(pool[i], keep_dashes)
        if size + len(p) > target and picked:
            break
        picked.append(p)
        size += len(p) + 2
    return picked, len(pool)


def main():
    ap = argparse.ArgumentParser(description="Build a style sample from a DRM-free EPUB.")
    ap.add_argument("epub")
    ap.add_argument("-o", "--output", default="my_writing.txt")
    ap.add_argument("--chars", type=int, default=5800, help="target size (default 5800)")
    ap.add_argument("--min-words", type=int, default=60)
    ap.add_argument("--max-words", type=int, default=260)
    ap.add_argument("--skip-front", type=float, default=0.05,
                    help="fraction of paragraphs to skip at the start (default 0.05)")
    ap.add_argument("--skip-back", type=float, default=0.05)
    ap.add_argument("--keep-dashes", action="store_true", help="keep em dashes in the sample")
    args = ap.parse_args()

    picked, pool = build(args.epub, args.chars, args.min_words, args.max_words,
                         args.skip_front, args.skip_back, args.keep_dashes)
    text = "\n\n".join(picked) + "\n"
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {args.output}: {len(picked)} passages, {len(text)} chars "
          f"(from {pool} candidate paragraphs)", file=sys.stderr)
    print("open it and delete anything that isn't the voice you want "
          "(dialogue-heavy bits, quotes, lists)", file=sys.stderr)


if __name__ == "__main__":
    main()
