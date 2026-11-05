#!/usr/bin/env python3
"""
rewrite.py - final-pass paraphraser.

Rewrites finished markdown/text unit by unit (paragraphs and list items) with a
local or any OpenAI-compatible model. Each rewrite must pass these gates:

  nonempty      the model returned something
  complete      the model did not stop at max_tokens
  placeholders  inline code, link targets and URLs are swapped for markers
                before rewriting; each must come back exactly once, link
                targets still attached to their [text], no invented markers
  facts         every number and name-like token survives
  no_emdash     no em dashes in the rewrite
  length        rewrite is 85-118% of the original word count
  meaning       embedding cosine similarity >= --min-sim
  overlap       share of the rewrite's n-grams also found in the original
                <= --max-overlap (n shrinks for short units; skipped for
                very short ones)

Failures are retried with specific feedback. If no attempt is nonempty,
complete, and keeps the placeholders and facts, the original text is kept and
the unit is flagged. Other failures keep the best attempt, flagged.

Code blocks, indented code, tables, blockquotes, headings (ATX and setext),
horizontal rules and front matter pass through untouched.

Exit codes: 0 all units passed, 3 some units flagged (output written),
1 backend/input error, 2 bad command-line arguments (argparse).
No third-party dependencies. Python 3.9+.
"""
import argparse
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = os.environ.get("REWRITE_BASE_URL", "http://localhost:11434/v1")
DEFAULT_MODEL = os.environ.get("REWRITE_MODEL", "qwen2.5:7b")
DEFAULT_EMBED_MODEL = os.environ.get("REWRITE_EMBED_MODEL", "nomic-embed-text")

SYSTEM_PROMPT = """You rewrite text.
Keep every fact, claim, name, number and the overall meaning exactly. Do not add, remove, or soften content.
Change the wording thoroughly: restructure sentences, reorder clauses, merge or split sentences, and pick different words and phrasings. Very few runs of four or more consecutive words from the original should survive.
Keep the same register and about the same length as the original. Keep any bold or italic markup.
Do not use em dashes.
Output only the rewritten text, with no preamble or commentary."""

PLACEHOLDER_NOTE = """
Markers like ⟦0⟧ stand for code, links or URLs. Copy every marker exactly once, unchanged, and keep it next to the words it belongs with."""


# ---------------------------------------------------------------- backend

class BackendError(Exception):
    pass


class Backend:
    def __init__(self, base_url, model, embed_model, api_key=None, timeout=300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embed_model = embed_model
        self.api_key = api_key
        self.timeout = timeout
        self.attempts = 3  # retries connection errors and 5xx with backoff
        # nomic embedding models expect a task prefix; use the same one on both sides
        self.embed_prefix = "clustering: " if "nomic" in embed_model else ""

    def _post(self, path, payload):
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload).encode()
        for i in range(self.attempts):
            last = i == self.attempts - 1
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:  # must come before URLError (subclass)
                body = e.read().decode("utf-8", "replace")[:500]
                if e.code >= 500 and not last:
                    time.sleep(2 ** i)
                    continue
                raise BackendError(f"{url} returned HTTP {e.code}: {body}")
            except (urllib.error.URLError, TimeoutError, socket.timeout) as e:  # 3.9: distinct
                if not last:
                    time.sleep(2 ** i)
                    continue
                reason = getattr(e, "reason", e)
                raise BackendError(f"could not reach {url}: {reason}. "
                                   f"Is the server running? (Ollama: ollama serve)")
            except json.JSONDecodeError:
                raise BackendError(f"{url} returned a non-JSON response")

    def chat(self, messages, temperature, max_tokens):
        """Returns (text, truncated)."""
        resp = self._post("/chat/completions", {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        try:
            choice = resp["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise BackendError(f"unexpected chat response: {str(resp)[:300]}")
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # reasoning models
        return text.strip(), choice.get("finish_reason") == "length"

    def embed(self, texts):
        resp = self._post("/embeddings", {
            "model": self.embed_model,
            "input": [self.embed_prefix + t for t in texts],
        })
        try:
            data = sorted(resp["data"], key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]
        except (KeyError, TypeError):
            raise BackendError(f"unexpected embeddings response: {str(resp)[:300]}")


# ---------------------------------------------------------------- text utils

PH_RE = re.compile(r"⟦\d+⟧")
PROTECT_RE = re.compile(
    r"`[^`\n]+`"                    # inline code
    r"|(?<=\])\((?:[^()\s]|\([^()\s]*\))+\)"      # link target ](url), one level of parens
    r"|https?://(?:[^\s()<>\]]|\([^\s()]*\))+"    # bare URL, e.g. ..._(disambiguation)
)


def protect(text):
    saved = []

    def sub(m):
        saved.append(m.group(0))
        return f"⟦{len(saved) - 1}⟧"

    return PROTECT_RE.sub(sub, text), saved


def restore(text, saved):
    for i, s in enumerate(saved):
        text = text.replace(f"⟦{i}⟧", s)
    return text


def plain(text):
    """Text with placeholders removed, for metrics."""
    return PH_RE.sub(" ", text)


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def ngrams(ws, n):
    return [tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)]


def overlap(source, rewrite, n=5):
    """Share of the rewrite's n-grams that also occur in the source, plus the
    surviving phrases. Only meaningful alongside the length gate, since the
    denominator is the rewrite's own length."""
    src = set(ngrams(words(source), n))
    out = ngrams(words(rewrite), n)
    if not out:
        return 0.0, []
    hits = [g for g in out if g in src]
    kept = list(dict.fromkeys(" ".join(g) for g in hits))
    return len(hits) / len(out), kept


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def numbers(text):
    # drop thousands separators only ("1,000" -> "1000"); "1,5" stays distinct from "15"
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    return set(re.findall(r"\d+(?:[.,]\d+)*", text))


# tokens start at a word boundary, so "7B" is one token (a strong name), not "7" + "B"
NAME_TOKEN_RE = re.compile(r"(?<![\w.])[A-Za-z0-9][\w'-]*(?:\.[A-Za-z0-9][\w-]*)*")


EMPH_RE = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")


def is_compound(tok):
    """Unambiguous name shapes: acronyms (AI, GPT), dotted (Venice.ai, U.S.),
    internal caps (OpenAI, iPhone), digits (GPT-4)."""
    return ((len(tok) > 1 and tok.isupper()) or "." in tok
            or any(c.isdigit() for c in tok) or any(c.isupper() for c in tok[1:]))


def names(text):
    """Returns (strong, weak).
    strong: compound name shapes anywhere. Losing one is a hard failure.
    weak:   plain capitalized words mid-sentence (Yara, Haifa), excluding words
            inside **emphasis** and words that also appear lowercase in the
            text. Losing one only flags the unit, since these are sometimes
            just Title Case or emphasis."""
    emph = {t for m in EMPH_RE.finditer(text) for t in NAME_TOKEN_RE.findall(m.group(2))}
    lower = {t for t in NAME_TOKEN_RE.findall(text) if t[0].islower()}
    strong, weak = set(), set()
    for sent in re.split(r"(?<=[.!?:;])\s+|\n", text):
        for pos, tok in enumerate(NAME_TOKEN_RE.findall(sent)):
            if tok == "I" or not any(c.isupper() for c in tok):
                continue
            if is_compound(tok):
                strong.add(tok)
            elif pos > 0 and tok[0].isupper() and tok not in emph \
                    and tok.lower() not in lower:
                weak.add(tok)
    return strong, weak


# ---------------------------------------------------------------- segmenting

LIST_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")   # --- *** ___ (also setext "-" underline)
SETEXT_RE = re.compile(r"^\s*(=+|-+)\s*$")


def classify(lines):
    """Turn one blank-line-separated chunk into (kind, text) blocks.
    kinds: keep (pass through), prose, list.
    Separators inside a chunk split it: a ===/--- line under text is a setext
    heading (kept with its title line); any other rule is kept on its own."""
    for idx, l in enumerate(lines):
        if idx > 0 and SETEXT_RE.match(l):
            before, rest = lines[:idx - 1], lines[idx + 1:]
            return ((classify(before) if before else [])
                    + [("keep", lines[idx - 1] + "\n" + l)]
                    + (classify(rest) if rest else []))
        if RULE_RE.match(l):
            before, rest = lines[:idx], lines[idx + 1:]
            return ((classify(before) if before else []) + [("keep", l)]
                    + (classify(rest) if rest else []))
    text = "\n".join(lines)
    if all(l.startswith(("    ", "\t")) for l in lines):
        return [("keep", text)]                       # indented code
    if any(l.lstrip().startswith("|") for l in lines):
        return [("keep", text)]                       # table
    if all(l.lstrip().startswith(">") for l in lines):
        return [("keep", text)]                       # blockquote
    if HEADING_RE.match(lines[0]):
        rest = lines[1:]
        return [("keep", lines[0])] + (classify(rest) if rest else [])
    k = next((i for i, l in enumerate(lines) if LIST_RE.match(l)), None)
    if k is None:
        return [("prose", text)]
    if k > 0:
        return [("prose", "\n".join(lines[:k])), ("list", "\n".join(lines[k:]))]
    return [("list", text)]


def segment(text):
    """Returns (blocks, warnings)."""
    lines = text.split("\n")
    blocks, para, warnings, i = [], [], [], 0

    def flush():
        if para:
            blocks.extend(classify(para))
            para.clear()

    if lines and lines[0].strip() == "---":           # YAML front matter
        j = 1
        while j < len(lines) and lines[j].strip() not in ("---", "..."):
            j += 1
        # only real YAML (has a key: line), not a document opening with a rule
        if j < len(lines) and any(":" in l for l in lines[1:j]):
            blocks.append(("keep", "\n".join(lines[:j + 1])))
            i = j + 1

    while i < len(lines):
        s = lines[i].strip()
        if s.startswith(("```", "~~~")):              # fenced code, closed or not
            flush()
            fence, j = s[:3], i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            if j >= len(lines):
                warnings.append(f"unclosed code fence at line {i + 1}: everything after it "
                                f"is left unchanged")
            blocks.append(("keep", "\n".join(lines[i:j + 1])))
            i = j + 1
            continue
        if not s:
            flush()
        else:
            para.append(lines[i])
        i += 1
    flush()
    return blocks, warnings


def list_items(text):
    """[[marker, text], ...]; continuation lines are folded into their item."""
    items = []
    for line in text.split("\n"):
        m = LIST_RE.match(line)
        if m:
            items.append([m.group(1), m.group(2)])
        elif items:
            items[-1][1] += " " + line.strip()
        else:
            items.append(["", line])
    return items


# ---------------------------------------------------------------- rewriting

def build_messages(text, prev, style, feedback, has_placeholders):
    system = SYSTEM_PROMPT + (PLACEHOLDER_NOTE if has_placeholders else "")
    if style:
        system += ("\n\nMatch the voice, rhythm and word choice of this sample of the "
                   "author's own writing (do not copy its content):\n<sample>\n"
                   + style + "\n</sample>")
    user = ""
    if prev:
        user += f"Preceding text, for flow only (do not rewrite it):\n{prev}\n\n"
    user += f"Rewrite this:\n{text}"
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if feedback:
        msgs.append({"role": "assistant", "content": feedback[0]})
        msgs.append({"role": "user", "content": feedback[1]})
    return msgs


_STYLE_GRAMS = {}


def copied_from_style(style, src, out, n=6):
    """Phrases (n-grams) in the rewrite that come from the style sample and not
    from the source: the model lifting the sample author's actual sentences."""
    if not style:
        return []
    if style not in _STYLE_GRAMS:
        _STYLE_GRAMS.clear()
        _STYLE_GRAMS[style] = set(ngrams(words(style), n))
    sg = _STYLE_GRAMS[style]
    src_g = set(ngrams(words(src), n))
    return list(dict.fromkeys(" ".join(g) for g in ngrams(words(out), n)
                              if g in sg and g not in src_g))


class Result:
    def __init__(self, text, ok, tries, ov, sim, lr, reason, src_plain, out_plain, kept=False):
        self.text, self.ok, self.tries = text, ok, tries
        self.ov, self.sim, self.lr, self.reason = ov, sim, lr, reason
        self.src_plain, self.out_plain = src_plain, out_plain
        self.kept = kept  # True when the original was kept instead of a rewrite


def marker_problems(out, saved):
    """Markers that are missing, duplicated, invented, or (for links) detached
    from their [anchor text]."""
    probs = []
    for i, s in enumerate(saved):
        m = f"⟦{i}⟧"
        c = out.count(m)
        if c != 1:
            probs.append(f"{m} appears {c} times, needs exactly 1")
        elif s.startswith("(") and f"]{m}" not in out:
            probs.append(f"{m} must directly follow the closing ] of its link text")
    valid = {f"⟦{i}⟧" for i in range(len(saved))}
    probs += [f"{m} does not exist, remove it" for m in sorted(set(PH_RE.findall(out)) - valid)]
    return probs


def rewrite_unit(backend, text, prev, args, style):
    prot, saved = protect(text)
    src = plain(prot)
    nsrc = len(words(src))
    n = args.ngram if nsrc >= 3 * args.ngram else max(2, args.ngram - 2)
    use_overlap = nsrc >= args.overlap_min_words
    src_emb = None if args.no_sim else backend.embed([src])[0]
    src_nums = set() if args.no_fact_check else numbers(src)
    src_strong, src_weak = (set(), set()) if args.no_fact_check else names(src)
    max_tokens = max(256, nsrc * 3)

    best, best_key, feedback, tries = None, None, None, 0
    for attempt in range(args.retries + 1):
        tries += 1
        temp = min(args.temperature + 0.1 * attempt, 1.3)
        out, truncated = backend.chat(build_messages(prot, prev, style, feedback, bool(saved)),
                                      temp, max_tokens)
        o = plain(out)
        lr = len(words(o)) / nsrc if nsrc else 0.0
        ov, kept = overlap(src, o, n) if use_overlap else (0.0, [])
        sim = 1.0 if args.no_sim else (cosine(src_emb, backend.embed([o])[0]) if o.strip() else 0.0)
        ph_probs = marker_problems(out, saved)
        miss_facts = sorted(src_nums - numbers(o)) + sorted(x for x in src_strong if x not in o)
        miss_names = sorted(x for x in src_weak if x not in o)
        copied = copied_from_style(style, src, o)

        gates = {
            "nonempty": bool(o.strip()),
            "complete": not truncated,
            "placeholders": not ph_probs,
            "facts": not miss_facts,
            "names": not miss_names,
            "no_copy": not copied,
            "length": args.min_len <= lr <= args.max_len,
            "meaning": sim >= args.min_sim,
            "no_emdash": "—" not in out,
            "overlap": ov <= args.max_overlap,
        }
        passes = all(gates.values())
        # hard gates first, then soft gates, then least overlap, then closest length
        key = (passes, gates["nonempty"], gates["complete"], gates["placeholders"],
               gates["facts"], gates["names"], gates["no_copy"], gates["meaning"], gates["length"],
               gates["no_emdash"], -ov, -abs(1.0 - lr))
        if best_key is None or key > best_key:
            best_key = key
            best = (out, o, ov, sim, lr, gates)
        if passes:
            break

        notes = []
        if not gates["nonempty"]:
            notes.append("That response was empty.")
        if truncated:
            notes.append("That response was cut off. Keep the rewrite about as long as the original.")
        if ph_probs:
            notes.append("Marker problems: " + "; ".join(ph_probs))
        if not gates["no_emdash"]:
            notes.append("Do not use em dashes (—). Use commas, colons, parentheses or separate "
                         "sentences instead.")
        if miss_facts or miss_names:
            notes.append("Keep these exactly as written in the original: "
                         + ", ".join((miss_facts + miss_names)[:15]))
        if copied:
            notes.append("These phrases come from the style sample. Imitate its voice, "
                         "never its wording: " + "; ".join(f'"{c}"' for c in copied[:8]))
        if not gates["length"]:
            notes.append(f"That version is {lr:.0%} of the original length. "
                         f"Keep it close to the original length.")
        if not gates["meaning"]:
            notes.append("That version drifted from the original meaning. Keep every point, "
                         "change only the wording.")
        if not gates["overlap"] and kept:
            notes.append("These phrases are still worded like the original. Rephrase them too: "
                         + "; ".join(f'"{k}"' for k in kept[:12]))
        feedback = (out, "\n".join(notes) + "\nRewrite the original again. Output only the text.")

    out, o, ov, sim, lr, gates = best
    failed = [g for g, ok in gates.items() if not ok]
    if not all(gates[g] for g in ("nonempty", "complete", "placeholders", "facts")):
        # never ship a rewrite that is empty, cut off, or lost code, links or facts
        return Result(text, False, tries, 1.0, 1.0, 1.0,
                      "kept original (" + ", ".join(failed) + ")", src, src, kept=True)
    return Result(restore(out, saved), not failed, tries, ov, sim, lr,
                  ", ".join(failed), src, o)


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Final-pass paraphraser using a local model.")
    p.add_argument("input", help="input file, or - for stdin")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    p.add_argument("--api-key", default=os.environ.get("REWRITE_API_KEY"),
                   help="prefer the REWRITE_API_KEY env var; argv is visible to other processes")
    p.add_argument("--style", help="file with a sample of your own writing to match")
    p.add_argument("--max-overlap", type=float, default=0.05,
                   help="max share of the rewrite's n-grams found in the original (default 0.05)")
    p.add_argument("--min-sim", type=float, default=0.85,
                   help="min embedding similarity to the original (default 0.85)")
    p.add_argument("--min-len", type=float, default=0.85, help="min length ratio (default 0.85)")
    p.add_argument("--max-len", type=float, default=1.18, help="max length ratio (default 1.18)")
    p.add_argument("--ngram", type=int, default=5,
                   help="n-gram size for overlap; shrinks to n-2 on short units (default 5)")
    p.add_argument("--overlap-min-words", type=int, default=12,
                   help="units shorter than this skip the overlap gate (default 12)")
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--min-words", type=int, default=6,
                   help="units shorter than this pass through unchanged (default 6)")
    p.add_argument("--no-sim", action="store_true", help="skip the meaning gate")
    p.add_argument("--no-fact-check", action="store_true", help="skip the numbers/names gate")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args()
    args.retries = max(0, args.retries)
    args.ngram = max(2, args.ngram)
    if args.min_len > args.max_len:
        p.error("--min-len must be <= --max-len")  # argparse usage errors exit 2

    try:
        if args.input == "-":
            text = sys.stdin.buffer.read().decode("utf-8")
        else:
            with open(args.input, encoding="utf-8") as f:
                text = f.read()
        style = None
        if args.style:
            with open(args.style, encoding="utf-8") as f:
                style = f.read()[:6000]
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    backend = Backend(args.base_url, args.model, args.embed_model, args.api_key)
    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))

    blocks, warnings = segment(text)
    for w in warnings:
        log(f"warning: {w}")

    def eligible(t):
        return len(words(plain(protect(t)[0]))) >= args.min_words

    total = sum(1 for k, b in blocks if k == "prose" and eligible(b))
    total += sum(1 for k, b in blocks if k == "list" for _, t in list_items(b) if eligible(t))

    out_blocks, prev, flagged, done = [], None, [], []
    n = 0

    def run(unit_text):
        nonlocal n, prev
        n += 1
        r = rewrite_unit(backend, unit_text, prev, args, style)
        status = "ok" if r.ok else f"FLAG: {r.reason}"
        if not r.ok:
            flagged.append(n)
        log(f"[{n}/{total}] overlap {r.ov:5.1%}  sim {r.sim:.3f}  len {r.lr:4.0%}  "
            f"tries {r.tries}  {status}")
        done.append(r)
        prev = r.text
        return r.text

    # Flow context (prev) is the last prose seen. Code, tables and headings are
    # deliberately not used as context: they are noise for a prose rewrite.
    error = None
    for bi, (kind, block) in enumerate(blocks):
        mark = (len(done), len(flagged), n)  # roll back stats if this block is abandoned
        try:
            if kind == "keep":
                piece = block
            elif kind == "prose":
                if eligible(block):
                    piece = run(block)
                else:
                    piece = block
                    prev = block
            else:  # list
                lines = []
                for marker, item in list_items(block):
                    if eligible(item):
                        lines.append(marker + run(item))
                    else:
                        lines.append(marker + item)
                        prev = item
                piece = "\n".join(lines)
        except BackendError as e:
            error = e
            del done[mark[0]:]
            del flagged[mark[1]:]
            n = mark[2]
            out_blocks.extend(b for _, b in blocks[bi:])  # rest left as original
            break
        out_blocks.append(piece)

    result = "\n\n".join(out_blocks) + "\n"

    rewritten = [r for r in done if not r.kept]
    kept = len(done) - len(rewritten)
    if rewritten:
        doc_ov, _ = overlap("\n".join(r.src_plain for r in rewritten),
                            "\n".join(r.out_plain for r in rewritten), args.ngram)
        log(f"\nrewritten units: {len(rewritten)}, their overall {args.ngram}-gram overlap: "
            f"{doc_ov:.1%}")
    if kept:
        log(f"units kept as original (lost facts/markers or empty): {kept}")
    if flagged:
        log(f"flagged units (review by hand): {flagged}")
    if error:
        print(f"\nerror: {error}\nstopped early; the rest of the document is unchanged",
              file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
        log(f"wrote {args.output}")
    else:
        sys.stdout.buffer.write(result.encode("utf-8"))

    if error:
        return 1
    return 3 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
