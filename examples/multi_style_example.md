# Multi-Style Example

## Problem

You write different kinds of content:
- Technical blog posts (precise, structured)  
- Personal essays (casual, reflective)
- Explanatory articles (clear, pedagogical)

A style sample from just one book might pull everything toward that single register.

## Solution

Extract samples from multiple books that represent the range of voices you use:

```bash
python3 multi_style.py \
  technical_writing.epub \
  personal_essays.epub \
  explanatory_prose.epub \
  -o my_combined_style.txt
```

The result: ~6000 chars total, divided evenly (2000 chars each if you pass three books).

## What you get

The rewrite model sees all three registers in its style sample:

- **Technical voice**: precise terminology, structured arguments
- **Personal voice**: looser phrasing, first-person flow  
- **Explanatory voice**: clear definitions, analogies, examples

When it rewrites your draft, it draws from all three, picking the register that
fits your content rather than forcing everything into one mold.

## Usage

```bash
# Build the combined style once
python3 multi_style.py book1.epub book2.epub book3.epub -o combined_style.txt

# Use it for all rewrites
python3 rewrite.py draft.md -o final.md --style combined_style.txt
```

## Recommendations

- Use 2-4 books (more dilutes each voice too much)
- Pick books that match your actual output mix
- Open `combined_style.txt` and delete dialogue, quotes, or overly literary passages
- The `no_copy` gate prevents the model from lifting actual sentences from any book

## Notes

The style sample is just a reference - the model tries to match voice/rhythm/word
choice, not content. If your draft is technical, it'll lean on the technical
passages from the sample. If it's narrative, it'll lean on those.

All style files stay local and are gitignored.
