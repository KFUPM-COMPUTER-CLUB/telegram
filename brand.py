"""brand.py - how the bot looks and sounds, following the Computer Club brand kit (v1.0).

The rules that matter for Telegram (from the brand guidelines):
- Voice: short, direct sentences. Technical, confident, a little playful. No hype words.
- No em dashes. Use a period, a comma or "to".
- One emoji per message at most (buttons don't count).
- Kickers are small caps labels joined by a middle dot: REMINDER · TOMORROW.
  Telegram has no fonts or colors in text, so kickers use <code> (monospace, like JetBrains Mono)
  and headlines use bold (like Poppins Bold).
- Every post in a group ends with @kfupm_cc and #MaxThroughput.

Every message built here is HTML, so send it with parse_mode=ParseMode.HTML and pass any
text typed by a user through esc() first.
"""
import html
import re
from urllib.parse import urlparse

FOOTER_TEXT = "@kfupm_cc · #MaxThroughput"
FOOTER = f"<code>{FOOTER_TEXT}</code>"

MAX_URL_LENGTH = 2000


def esc(text):
    """Makes user text safe to put inside an HTML message."""
    return html.escape(str(text), quote=False)


def kicker(*parts):
    """'REMINDER · TOMORROW' in monospace."""
    return "<code>" + " · ".join(esc(part).upper() for part in parts) + "</code>"


def headline(text):
    return f"<b>{esc(text)}</b>"


def post(*blocks):
    """Joins the blocks of a group post with blank lines and adds the club footer."""
    return "\n\n".join([*(block for block in blocks if block), FOOTER])


def visible_length(html_text):
    """How many characters Telegram counts for an HTML message (tags don't count)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def formatted_text(message):
    """The message's text (or caption) as HTML, keeping bold, italics, links etc.
    Premium custom emoji become normal emoji, because bots can't send them."""
    html_text = message.caption_html if message.caption is not None else message.text_html
    return re.sub(r'<tg-emoji emoji-id="[^"]*">(.*?)</tg-emoji>', r"\1", html_text or "", flags=re.DOTALL).strip()


def clean_url(text):
    """Returns a link Telegram will accept as a button, or None.
    'cckfupm.vercel.app' becomes 'https://cckfupm.vercel.app'."""
    text = text.strip()
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text
    if len(text) > MAX_URL_LENGTH or any(char.isspace() for char in text):
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https") or "." not in parsed.netloc:
        return None
    return text
