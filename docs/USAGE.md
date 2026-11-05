# rewrite.py

Final-pass paraphraser. Runs once on finished text, locally, and rewrites each
paragraph until almost none of the original 5-word sequences survive while the
meaning stays the same.

## Setup (one time)

1. Install Ollama: https://ollama.com (Mac, Windows, Linux)
2. Pull a rewrite model and an embedding model:

   ```
   ollama pull qwen2.5:7b
   ollama pull nomic-embed-text
   ```

3. Pin the model so a future `ollama pull` can't change it under you:

   ```
   ollama cp qwen2.5:7b rewrite-pinned
   ```

   Then pass `--model rewrite-pinned` (or set `REWRITE_MODEL=rewrite-pinned`).

No pip installs. Python 3.9+ standard library only.

## Run

```
python3 rewrite.py draft.md -o final.md
```

With a sample of your own writing so the output matches your voice:

```
python3 rewrite.py draft.md -o final.md --style my_writing.txt
```

From the clipboard (Mac):

```
pbpaste | python3 rewrite.py - | pbcopy
```

Progress goes to stderr, one line per paragraph or list item:

```
[3/12] overlap  2.1%  sim 0.912  len 104%  tries 2  ok
```

## Gates

Every rewrite has to pass all of them. Failures are retried with specific
feedback (marker problems, missing facts, length, em dashes, surviving phrases).

| gate | rule |
|---|---|
| nonempty | the model returned text |
| complete | the model didn't stop at its token limit mid-sentence |
| placeholders | inline code, link targets and URLs become `⟦0⟧` markers before rewriting; each must come back exactly once, link targets must stay attached to their `[text]`, and invented markers fail |
| facts | every number and every unambiguous name survives: acronyms (`AI`), dotted names (`Venice.ai`), internal caps (`OpenAI`), tokens with digits (`GPT-4`) |
| names | plain capitalized words mid-sentence (`Yara`, `Haifa`) survive. Soft: failing it flags the unit but keeps the rewrite. Words in `**bold**`/`*italics*` and words that also appear lowercase are not counted |
| no_emdash | no em dashes |
| length | rewrite is 85–118% of the original word count, so padding can't game the overlap score |
| meaning | embedding cosine similarity ≥ 0.85 (catches topic drift, not fact swaps; that's what the facts gate is for) |
| overlap | ≤ 5% of the rewrite's n-grams appear in the original; n drops from 5 to 3 on short units, and units under 12 words skip this gate |

If no attempt is nonempty, complete, and keeps the placeholders and facts,
**the original text is kept** and the unit is flagged. A unit that fails only
names, length, meaning, em dash or overlap keeps its best attempt and is flagged.
Either way, read flagged units by hand. The summary's overlap figure covers
rewritten units only; kept-original units are counted separately.

Passed through untouched: fenced and indented code, tables, blockquotes,
headings (`#` and underlined), horizontal rules, YAML front matter. Lists are
rewritten item by item with their markers kept. An unclosed code fence
triggers a warning, since everything after it passes through.

Connection errors and 5xx responses are retried 3 times with backoff. If the
backend still fails, everything finished so far is written, the rest of the
document is left as the original, and the exit code is 1.

Exit codes: `0` all passed, `3` some units flagged (output still written),
`1` backend or input error, `2` bad command-line arguments.

## Style sample from an EPUB

```
python3 epub_to_style.py book.epub -o my_writing.txt
```

This picks prose passages spread evenly across the book, about 5800 characters
(`rewrite.py` reads the first 6000), skipping front matter, back matter,
notes, headings and short lines. Em dashes are replaced with commas unless you
pass `--keep-dashes`. It only works on DRM-free EPUBs; Kindle and Apple Books
purchases are usually locked. Open the result and delete anything that isn't
the voice you want.

When a style file is used, the `no_copy` gate (soft) flags any rewrite that
contains 6-word phrases from the sample that weren't in your draft, so the
sample author's actual sentences can't end up in your text.

## Knobs

| flag | default | meaning |
|---|---|---|
| `--max-overlap` | 0.05 | overlap gate threshold |
| `--min-sim` | 0.85 | meaning gate threshold |
| `--min-len` / `--max-len` | 0.85 / 1.18 | length gate bounds |
| `--ngram` | 5 | n-gram size for overlap |
| `--overlap-min-words` | 12 | shorter units skip the overlap gate |
| `--retries` | 4 | extra attempts per unit |
| `--min-words` | 6 | shorter units pass through unchanged |
| `--style` | none | file of your own writing to imitate (first 6000 chars used) |
| `--no-sim` | off | skip the meaning gate |
| `--no-fact-check` | off | skip the facts gate (e.g. if it trips on Title Case you don't care about) |

## Hosted backend

Any OpenAI-compatible endpoint works:

```
python3 rewrite.py draft.md -o final.md \
  --base-url https://api.example.com/v1 --model some-model \
  --embed-model some-embedding-model --api-key $KEY
```

Local stays the default: it's the only setup where you control the sampler.

## Notes

- A 7-8B model gives noticeably better voice than 1-3B. Drop to a smaller one
  if it's too slow on your machine.
- The overlap number is a proxy. It measures how much of the original wording
  survived, not a detector score.
- Units under 6 words pass through unchanged by design; they carry very little
  signal either way. Lower `--min-words` if you want them rewritten too.
- The facts gate doesn't catch everything. Negation flips ("will" to "won't"),
  hedges ("may" to "will"), units, and ordinary sentence-initial names
  ("Haifa reported...") get past it. Skim the output for those.
- The facts gate is strict on purpose. Spelled-out numbers ("five" vs "5")
  will trip it; the retry feedback tells the model to keep them as written.
