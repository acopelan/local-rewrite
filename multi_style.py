#!/usr/bin/env python3
"""
multi_style.py - build a combined style sample from multiple EPUBs.

Extracts prose passages from multiple DRM-free EPUBs and combines them into
one style file. The target size is divided evenly across all books, so the
model gets a broader vocabulary and style palette to draw from.

Useful when you want the rewriter to have exposure to different registers:
technical writing, narrative, explanatory prose, etc. all in one sample.

Usage:
    python3 multi_style.py book1.epub book2.epub book3.epub -o combined_style.txt
    python3 multi_style.py *.epub -o my_style.txt

The combined sample stays under 6000 chars (rewrite.py's limit), distributed
evenly across the input books.
"""
import argparse
import sys
from epub_to_style import build

def main():
    ap = argparse.ArgumentParser(
        description="Build a combined style sample from multiple DRM-free EPUBs.")
    ap.add_argument("epubs", nargs="+", help="EPUB files to combine")
    ap.add_argument("-o", "--output", default="my_writing.txt")
    ap.add_argument("--chars", type=int, default=5800, 
                    help="total target size across all books (default 5800)")
    ap.add_argument("--min-words", type=int, default=60)
    ap.add_argument("--max-words", type=int, default=260)
    ap.add_argument("--skip-front", type=float, default=0.05,
                    help="fraction of paragraphs to skip at the start of each book (default 0.05)")
    ap.add_argument("--skip-back", type=float, default=0.05)
    ap.add_argument("--keep-dashes", action="store_true", 
                    help="keep em dashes in the sample")
    ap.add_argument("--separator", action="store_true",
                    help="add a blank line between books in the output")
    args = ap.parse_args()
    
    if len(args.epubs) == 0:
        ap.error("provide at least one EPUB file")
    
    # Divide the character budget evenly across books
    per_book = args.chars // len(args.epubs)
    
    all_passages = []
    total_chars = 0
    stats = []
    
    for i, epub_path in enumerate(args.epubs, 1):
        print(f"\n[{i}/{len(args.epubs)}] Processing {epub_path}...", file=sys.stderr)
        try:
            picked, pool = build(
                epub_path, per_book, args.min_words, args.max_words,
                args.skip_front, args.skip_back, args.keep_dashes
            )
            passage_text = "\n\n".join(picked)
            all_passages.append(passage_text)
            total_chars += len(passage_text)
            stats.append((epub_path, len(picked), len(passage_text), pool))
            print(f"  ✓ extracted {len(picked)} passages, {len(passage_text)} chars "
                  f"(from {pool} candidates)", file=sys.stderr)
        except SystemExit as e:
            print(f"  ✗ skipping {epub_path}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  ✗ skipping {epub_path}: {e}", file=sys.stderr)
            continue
    
    if not all_passages:
        sys.exit("error: no passages extracted from any EPUB")
    
    # Combine with optional separator
    separator = "\n\n\n" if args.separator else "\n\n"
    combined = separator.join(all_passages) + "\n"
    
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(combined)
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Wrote {args.output}: {total_chars} chars from {len(all_passages)} books", 
          file=sys.stderr)
    print(f"\nBreakdown:", file=sys.stderr)
    for path, passages, chars, pool in stats:
        print(f"  {path}: {passages} passages, {chars} chars", file=sys.stderr)
    print(f"\nOpen {args.output} and delete anything that isn't the voice you want", 
          file=sys.stderr)
    print(f"(dialogue-heavy bits, quotes, lists)", file=sys.stderr)


if __name__ == "__main__":
    main()
