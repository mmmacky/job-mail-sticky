# -*- coding: utf-8 -*-
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


def safe_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in ('http', 'https') and bool(parsed.hostname) and not any(c.isspace() for c in value)
    except ValueError:
        return False


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if self.skip: return
        if tag in ('p', 'div', 'br', 'tr', 'li', 'h1', 'h2'):
            self.parts.append('\n')
        if tag == 'a':
            value = dict(attrs).get('href', '').strip()
            self.anchor = value if safe_url(value) else None

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip: return
        if tag == 'a' and self.anchor:
            self.parts.append(' <' + self.anchor + '> ')
            self.anchor = None
        if tag in ('p', 'div', 'tr', 'li', 'h1', 'h2'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.skip: self.parts.append(value)


def readable_html(value):
    parser = ReadableHTML()
    parser.feed(value)
    parser.close()
    return ''.join(parser.parts)


def links_in(text):
    result = []
    for match in re.finditer(r'https?://[^\s<>"\u3000\u3001\u3002\uff0c\uff1b\uff09]+', text, re.I):
        value = match[0].rstrip('.,;!')
        while value.endswith(')') and value.count(')') > value.count('('):
            value = value[:-1]
        if safe_url(value) and value not in result:
            result.append(value)
    return result
