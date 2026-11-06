# local-rewrite

A final-pass rewriter for finished text that runs entirely on your own machine,
with verification gates so a small local model can't quietly break your
document.

Draft wherever you like. When the piece is done, one command rewrites it
paragraph by paragraph through a local model (Ollama, Qwen2.5 7B by default),
in a voice you choose, and checks every rewrite before it ships.

```
python3 rewrite.py draft.md -o final.md --style my_writing.txt
```

```
[1/9] overlap  1.8%  sim 0.921  len 103%  tries 1  ok
[2/9] overlap  0.0%  sim 0.904  len  97%  tries 2  ok
[3/9] overlap  4.2%  sim 0.887  len 112%  tries 5  FLAG: names
...
rewritten units: 9, their overall 5-gram overlap: 1.6%
flagged units (review by hand): [3]
```

## Why the gates matter

A 7B model will happily drop a number, invent a name, merge a link into a
sentence, or pad a paragraph. The model is the easy part; the validator is the
product. Each unit (a paragraph or list item) has to pass:

| gate | type | rule |
|---|---|---|
| nonempty | hard | the model returned text |
| complete | hard | the model didn't stop at its token limit |
| placeholders | hard | inline code, links and URLs are swapped for markers before rewriting and must each come back exactly once, still attached to their link text |
| facts | hard | every number and unambiguous name (`OpenAI`, `GPT-4`, `Venice.ai`) survives |
| names | soft | plain capitalized names mid-sentence survive |
| no_copy | soft | no 6-word phrases lifted from the style sample |
| length | soft | 85-118% of the original word count, so padding can't game the overlap score |
| meaning | soft | embedding cosine similarity to the original >= 0.85 |
| no_emdash | soft | no em dashes |
| overlap | soft | <= 5% of the rewrite's n-grams appear in the original |

Failures are retried with specific feedback (which marker, which fact, which
phrases survived). If no attempt passes the hard gates, **the original
paragraph is kept** and flagged. Soft failures keep the best attempt, flagged.
Nothing breaks silently.

Code blocks, tables, headings, quotes, rules and front matter pass through
untouched.

## Design notes

- **Stdlib only.** Python 3.9+, no dependencies. Talks to any
  OpenAI-compatible endpoint; local Ollama is the default.
- **Exit codes for automation.** `0` clean, `3` some units flagged, `1`
  backend or input error, `2` bad arguments. Partial output is written even
  when the backend dies mid-run.
- **Agent-friendly.** Built to be called by a coding agent as the last step of
  a writing workflow. See [`examples/AGENTS.md`](examples/AGENTS.md).
- **Style from a book.** `epub_to_style.py` builds a style sample from a
  DRM-free EPUB you own. `multi_style.py` combines samples from multiple
  EPUBs into one richer style file for broader vocabulary and register.
  Samples stay local and are gitignored.

## Docs

- [Usage and all flags](docs/USAGE.md)
- [Full stack setup: Ollama, agent, phone access](docs/SETUP.md)

## Tests

```
python3 -m unittest discover tests
```

Runs against a scripted fake model and an in-process mock server, so no model
download is needed.

## Known limits

The gates can't see meaning flips that keep the words and numbers intact:
negation ("will" to "won't"), hedges hardening ("may" to "will"), and names at
the start of a sentence. Skim the output for those.

## License

MIT
