"""Display fixes for model replies."""
from __future__ import annotations

import re

FONT_SIZE = 18          # px, the one text size of the app: keep `baseFontSize` in .streamlit/config.toml the same (it sizes the rest, tables included)
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def tidy_markdown(text: str) -> str:
    """`text` with a blank line put before a list that follows a plain line. Models often write `**Heading**` and the list on the next line:
    Markdown then folds "6. ... 7. ..." into the paragraph. Code blocks and indented continuation lines are left alone; the stored reply is not changed."""
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif (not fenced and LIST_ITEM.match(line) and out and out[-1].strip()
              and not LIST_ITEM.match(out[-1]) and not out[-1][0].isspace()):
            out.append("")
        out.append(line)
    return "\n".join(out)
