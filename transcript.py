"""Static, escaped HTML transcripts. No scripts, remote images or active Markdown."""
from html import escape
from urllib.parse import urlsplit

HEADER = '''<!doctype html><html lang="ro"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>RedFear • Arhivă tichet</title><style>
body{background:#202225;color:#eee;font:16px system-ui;max-width:960px;margin:32px auto;padding:20px}
article{background:#2f3136;padding:16px;margin:12px 0;border-radius:8px;overflow-wrap:anywhere}
pre{white-space:pre-wrap;font:inherit}small{color:#b9bbbe}a{color:#70baff}h1{color:#ff6b6b}
</style><h1>RedFear • Suport</h1>'''
FOOTER = '</html>'


def safe_link(url, label):
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ('https', 'http') and bool(parsed.netloc)
    except ValueError:
        valid = False
    if not valid:
        return escape(label)
    return f'<a rel="noreferrer noopener" href="{escape(url, quote=True)}">{escape(label)}</a>'


def render_message(msg):
    content = [escape(msg.content)]
    for embed in msg.embeds:
        content += [escape(embed.title or ''), escape(embed.description or '')]
        for field in embed.fields:
            content += [escape(field.name), escape(field.value)]
    for attachment in msg.attachments:
        content.append(safe_link(attachment.url, attachment.filename))
    return (f'<article><b>{escape(str(msg.author))}</b> <small>{msg.author.id} • '
            f'{escape(msg.created_at.isoformat())} • mesaj {msg.id}</small><pre>'
            + '\n'.join(content) + '</pre></article>')


async def export_parts(channel, summary, limit=1_000_000):
    """Yield complete HTML documents of bounded size without loading all history."""
    prefix = HEADER + '<pre>' + escape(summary) + '</pre>'
    current = prefix
    async for message in channel.history(limit=None, oldest_first=True):
        fragment = render_message(message)
        if len((current + fragment + FOOTER).encode()) > limit and current != prefix:
            yield (current + FOOTER).encode()
            current = prefix
        current += fragment
    yield (current + FOOTER).encode()
