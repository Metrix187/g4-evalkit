"""Turn notes/paper_draft.md into HTML and a PDF for the Kaggle writeup upload.

    /d/kaggleulturie/.venv/Scripts/python.exe tools/paper_build.py   # writes results/paper/paper.{html,pdf}

the pdf comes out of headless chrome, so nothing to install beyond python-markdown.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "notes" / "paper_draft.md"
OUT = REPO / "results" / "paper"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

CSS = """
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt; line-height: 1.38;
       max-width: 17cm; margin: 1.6cm auto; color: #111; }
h1 { font-size: 17pt; line-height: 1.2; margin: 0 0 .6em; }
h2 { font-size: 12.5pt; margin: 1.3em 0 .4em; }
h3 { font-size: 11pt; margin: 1.1em 0 .3em; }
p { margin: 0 0 .55em; text-align: justify; }
code { font-family: Consolas, monospace; font-size: 9pt; }
table { border-collapse: collapse; font-size: 8.6pt; margin: .5em 0 .8em; width: 100%; }
th, td { border: 1px solid #999; padding: 2px 5px; vertical-align: top; text-align: left; }
th { background: #eee; }
ol { padding-left: 1.4em; font-size: 9.2pt; }
li { margin-bottom: .15em; }
@page { size: A4; margin: 1.5cm; }
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    body = markdown.markdown(SRC.read_text(encoding="utf-8"), extensions=["tables"])
    byline = '<p><em>sky (Kaggle: goth55). Gemma 4 Developer Agent Paper Track, November 2026.</em></p>'
    body = body.replace("</h1>", "</h1>" + byline, 1)
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>Grading you can trust</title><style>{CSS}</style></head><body>{body}</body></html>"
    (OUT / "paper.html").write_text(html, encoding="utf-8")
    pdf = OUT / "paper.pdf"
    cmd = [str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={pdf}", (OUT / "paper.html").as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not pdf.exists():
        sys.exit(f"chrome gave no pdf: {r.stderr[-800:]}")
    print(f"{OUT / 'paper.html'}\n{pdf} ({pdf.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
