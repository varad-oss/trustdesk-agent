"""Spotlighting for untrusted text (defense level D1+).

Technique: delimiting + datamarking (Hines et al., 2024, "Defending Against Indirect
Prompt Injection Attacks With Spotlighting"). Untrusted text is wrapped in an
`<untrusted>` block and every whitespace run is replaced with a marker character, so
the model can tell, token by token, that the text is data. The system prompt explains
the marker. Any `<untrusted` / `</untrusted` inside the text is neutralised so a
payload cannot close the block early and "escape" into instruction space.
"""

from __future__ import annotations

import re

from trustdesk.config import DefenseLevel

DATAMARK = "ˆ"  # "ˆ" modifier circumflex: rare in normal text, one token-ish
_WS = re.compile(r"\s+")
_TAG = re.compile(r"</?\s*untrusted", re.IGNORECASE)


def datamark(text: str) -> str:
    return _WS.sub(DATAMARK, text.strip())


def wrap_untrusted(text: str, source: str, ref: str, level: DefenseLevel) -> str:
    """Return the text as the model should see it at this defense level."""
    if level < DefenseLevel.D1:
        return text
    safe = _TAG.sub("[tag-removed]", text)
    return f'<untrusted source="{source}" id="{ref}">{datamark(safe)}</untrusted>'
