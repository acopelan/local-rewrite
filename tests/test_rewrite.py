"""Tests for rewrite.py. No model needed: a scripted fake backend for the gate
logic, and an in-process mock server for the CLI and HTTP paths.

    python3 -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import rewrite as R  # noqa: E402

SRC = ("The vessel reported 1,200 tonnes of cargo near Haifa after the storm "
       "passed through the region yesterday.")
GOOD = ("Once the weather cleared the day before, the ship said it held 1200 "
        "tonnes of goods close to Haifa port.")


def args(**kw):
    d = dict(no_sim=True, no_fact_check=False, retries=2, temperature=0.8, ngram=5,
             max_overlap=0.05, min_sim=0.85, min_len=0.85, max_len=1.18,
             overlap_min_words=12, min_words=6)
    d.update(kw)
    return types.SimpleNamespace(**d)


class Fake:
    """Returns scripted outputs in order, repeating the last one."""

    def __init__(self, outs, truncated=False):
        self.outs, self.truncated, self.calls = list(outs), truncated, 0

    def chat(self, messages, temperature, max_tokens):
        self.calls += 1
        out = self.outs.pop(0) if len(self.outs) > 1 else self.outs[0]
        return out, self.truncated

    def embed(self, texts):
        return [[1.0, 0.0]] * len(texts)


def run(outs, src=SRC, style=None, truncated=False, **kw):
    return R.rewrite_unit(Fake(outs, truncated), src, None, args(**kw), style)


class TestMetrics(unittest.TestCase):
    def test_overlap_identical_and_disjoint(self):
        self.assertEqual(R.overlap(SRC, SRC)[0], 1.0)
        self.assertEqual(R.overlap(SRC, GOOD)[0], 0.0)

    def test_numbers_thousands_vs_decimal_comma(self):
        self.assertEqual(R.numbers("1,000 and 1,5 and 15"), {"1000", "1,5", "15"})

    def test_names_tiers(self):
        strong, weak = R.names("Venice.ai keeps it private. GPT-4 and OpenAI agreed, "
                               "said Yara. This is the **Core** idea.")
        self.assertEqual(strong, {"Venice.ai", "GPT-4", "OpenAI"})
        self.assertEqual(weak, {"Yara"})

    def test_model_size_is_one_token(self):
        strong, weak = R.names("We run a local Qwen 2.5 7B model on the Mac.")
        self.assertIn("7B", strong)
        self.assertNotIn("B", weak)


class TestGates(unittest.TestCase):
    def test_clean_rewrite_passes(self):
        r = run([GOOD])
        self.assertTrue(r.ok)
        self.assertEqual(r.text, GOOD)

    def test_padding_fails_length_then_recovers(self):
        pad = GOOD + " " + "It was a very long and rather eventful day indeed. " * 3
        r = run([pad, GOOD])
        self.assertTrue(r.ok)
        self.assertEqual(r.tries, 2)

    def test_lost_number_keeps_original(self):
        r = run([GOOD.replace("1200", "many")])
        self.assertTrue(r.kept)
        self.assertEqual(r.text, SRC)

    def test_empty_and_truncated_keep_original(self):
        self.assertTrue(run([""]).kept)
        self.assertTrue(run([GOOD], truncated=True).kept)

    def test_dropped_plain_name_flags_but_ships(self):
        s = "We sent the final report to Yara and the OpenAI team after the long review."
        r = run(["After the long review wrapped up, the finished report went to the OpenAI group."],
                src=s)
        self.assertFalse(r.ok)
        self.assertFalse(r.kept)
        self.assertIn("names", r.reason)

    def test_em_dash_flagged(self):
        r = run([GOOD.replace(", the ship", " — the ship")], retries=0)
        self.assertFalse(r.ok)
        self.assertIn("no_emdash", r.reason)

    def test_short_unit_skips_overlap(self):
        r = run(["Every Monday the crew checks all of the engines."],
                src="The crew inspects the engines every single Monday morning.")
        self.assertTrue(r.ok)


class TestPlaceholders(unittest.TestCase):
    S = ("Run `rewrite.py` with the style flag as described in [the docs](https://ex.com/a) "
         "before you publish anything.")
    OK = "Before publishing anything, follow [the guide]⟦1⟧ and call ⟦0⟧ using the flag for style."

    def test_roundtrip(self):
        r = run([self.OK], src=self.S)
        self.assertTrue(r.ok)
        self.assertIn("`rewrite.py`", r.text)
        self.assertIn("[the guide](https://ex.com/a)", r.text)

    def test_duplicate_detached_invented_all_keep_original(self):
        bad = [self.OK.replace("⟦0⟧", "⟦0⟧ ⟦0⟧"),
               self.OK.replace("[the guide]⟦1⟧", "the guide ⟦1⟧"),
               self.OK.replace("flag", "flag ⟦7⟧")]
        for b in bad:
            with self.subTest(b=b):
                self.assertTrue(run([b], src=self.S).kept)

    def test_url_with_parens(self):
        prot, saved = R.protect("See https://en.wikipedia.org/wiki/Mercury_(planet) now.")
        self.assertEqual(saved, ["https://en.wikipedia.org/wiki/Mercury_(planet)"])


class TestStyleCopy(unittest.TestCase):
    def test_lifted_phrase_is_flagged(self):
        style = "the harbor held its breath while the instruments kept their quiet ledger"
        copied = "Once the weather cleared the harbor held its breath while the ship held 1200 tonnes near Haifa."
        r = run([copied], style=style, retries=0, min_len=0, max_len=9, max_overlap=1)
        self.assertIn("no_copy", r.reason)


class TestSegment(unittest.TestCase):
    def test_kinds(self):
        doc = ("---\ntitle: x\n---\n# Heading\nIntro line under the heading with enough words.\n\n"
               "- first item has enough words to be rewritten here\n- second item\n\n"
               "| a | b |\n|---|---|\n\n    indented code\n\n> quote\n\n"
               "Title\n=====\n\n```\ncode\n\nmore\n```\n")
        kinds = [k for k, _ in R.segment(doc)[0]]
        self.assertEqual(kinds, ["keep", "keep", "prose", "list", "keep", "keep", "keep",
                                 "keep", "keep"])

    def test_unclosed_fence_warns(self):
        _, warnings = R.segment("Text here.\n\n```\nnever closed")
        self.assertEqual(len(warnings), 1)

    def test_rule_at_start_is_not_front_matter(self):
        kinds = [k for k, _ in R.segment("---\n\nA long enough paragraph of text.\n\n---\n")[0]]
        self.assertEqual(kinds, ["keep", "prose", "keep"])


# ---------------------------------------------------------------- CLI

class MockServer:
    """Answers chat and embeddings. fail_after: return 500 after N requests."""

    def __init__(self, fail_after=None, http_error=None):
        state = {"n": 0}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["n"] += 1
                if http_error or (fail_after is not None and state["n"] > fail_after):
                    self.send_response(http_error or 500)
                    self.end_headers()
                    return
                if self.path.endswith("/embeddings"):
                    out = {"data": [{"index": i, "embedding": [1.0, 0.0]}
                                    for i, _ in enumerate(body["input"])]}
                else:
                    out = {"choices": [{"finish_reason": "stop", "message": {"content": GOOD}}]}
                b = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def cli(url, text, *extra):
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "in.md"), os.path.join(d, "out.md")
        with open(src, "w") as f:
            f.write(text)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "rewrite.py"), src, "-o", out,
                            "--base-url", url, *extra], capture_output=True, text=True)
        result = None
        if os.path.exists(out):
            with open(out) as f:
                result = f.read()
        return p.returncode, result, p.stderr


class TestCLI(unittest.TestCase):
    def test_clean_run_exit_0(self):
        s = MockServer()
        try:
            code, out, _ = cli(s.url, f"# Title\n\n{SRC}\n\n```\ncode stays\n```\n")
        finally:
            s.close()
        self.assertEqual(code, 0)
        self.assertIn(GOOD, out)
        self.assertIn("code stays", out)

    def test_http_error_exit_1_with_status(self):
        s = MockServer(http_error=404)
        try:
            code, _, err = cli(s.url, SRC + "\n")
        finally:
            s.close()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 404", err)

    def test_backend_dies_writes_partial_output(self):
        s = MockServer(fail_after=3)
        try:
            code, out, _ = cli(s.url, f"{SRC}\n\n{SRC}\n")
        finally:
            s.close()
        self.assertEqual(code, 1)
        self.assertEqual(out.count(GOOD), 1)
        self.assertEqual(out.count(SRC), 1)

    def test_bad_args_exit_2(self):
        code, _, _ = cli("http://127.0.0.1:9/v1", SRC, "--min-len", "2", "--max-len", "1")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
