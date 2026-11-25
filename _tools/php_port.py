import html
import re


def sql_string(raw):
    escapes = {'n': '\n', 'r': '\r', 't': '\t', '0': '', 'Z': ''}
    text = re.sub(r"\\(.)|''", lambda m: escapes.get(m[1], m[1]) if m[1] else "'", raw, flags=re.S)
    # With magic_quotes on, the PHP app stored text with one or two extra addslashes layers. addslashes
    # never doubles quotes, so a stored '' is what the visitor typed.
    previous = None
    while previous != text:
        previous, text = text, re.sub(r"\\([\"'\\])", r"\1", text)
    return text


VALUE = re.compile(r"\s*(?:'((?:[^'\\]|\\.|'')*)'|(-?\d+)|NULL)\s*", re.S)


def read_row(text, i):
    values = []
    i += 1
    while True:
        m = VALUE.match(text, i)
        if not m:
            return None, i
        values.append(sql_string(m[1]) if m[1] is not None else int(m[2]) if m[2] else None)
        i = m.end()
        if text.startswith(',', i):
            i += 1
        elif text.startswith(')', i):
            return values, i + 1
        else:
            return None, i


def parse_dump(path, wanted):
    text = path.read_bytes().decode('cp1257', errors='replace')
    tables, skipped = {}, {}
    for insert in re.finditer(r'INSERT INTO `(\w+)` \(([^)]*)\) VALUES ', text):
        if insert[1] not in wanted:
            continue
        columns = re.findall(r'`(\w+)`', insert[2])
        end = text.find('CREATE TABLE', insert.end())
        region = text[insert.end():end if end > 0 else len(text)]
        rows, last, broken = [], 0, 0
        # The 2011 spam cleanup blanked lines inside multi-line rows; such broken rows fail to parse.
        for start in re.finditer(r'^\(', region, re.M):
            if start.start() < last:
                continue
            values, stop = read_row(region, start.start())
            if values is not None and len(values) == len(columns):
                rows.append(dict(zip(columns, values)))
                last = stop
            else:
                broken += 1
        tables[insert[1]], skipped[insert[1]] = rows, broken
    return tables, skipped


class Template:
    def __init__(self, text):
        self.text = text
        self.vars = {}

    def block(self, handle, name):
        pattern = re.compile(r'<!--\s+BEGIN %s\s+-->(.*)\n\s*<!--\s+END %s\s+-->' % (handle, handle), re.S)
        match = pattern.search(self.text)
        if not match:
            raise ValueError(f'template has no block {handle}')
        self.vars[handle] = match[1]
        self.text = pattern.sub(lambda _: '{%s}' % name, self.text)
        self.vars[name] = ''

    def set(self, **values):
        self.vars.update({k: str(v) for k, v in values.items()})

    def parse(self, target, handle, append=False):
        out = self.subst(self.vars[handle])
        self.vars[target] = self.vars.get(target, '') + out if append else out

    # PHPLib Template replaces variables one after another in the order they were first set; later
    # variables may fill placeholders that earlier values inserted, and some templates rely on that.
    def subst(self, text):
        for key, value in self.vars.items():
            text = text.replace('{%s}' % key, value)
        return text

    def render(self):
        return self.subst(self.text)


def hs(text):
    return html.escape(text or '', quote=False).replace('"', '&quot;')


def nl2br(text):
    return re.sub(r'(\r\n|\n\r|\n|\r)', r'<br />\1', text or '')


def php_round(x):
    return int(x + 0.5)


def paging(offset, count, per_page, css, link):
    step = php_round(count / (per_page * 6))
    current = offset // per_page + 1
    last = (count - 1) // per_page + 1
    pages = {0, 1, last}
    for i in range(1, last + 1):
        if step == 0 or i % step == 0:
            pages.add(i)
    pages.update(range(current - 2, current + 3))
    out = '' if current == 1 else f"<a href='{link}{(current - 2) * per_page}' class='{css}'>&lt;&lt;</a>"
    for i in range(1, last + 1):
        if i in pages and i - 1 in pages:
            out += f' <b>{i}</b>' if i == current else f" <a href='{link}{(i - 1) * per_page}'  class='{css}'>{i}</a>"
        elif i in pages:
            out += f" &middot;&middot;&middot; <a href='{link}{(i - 1) * per_page}'  class='{css}'>{i}</a>"
    if not (last == current or not count):
        out += f" <a href='{link}{current * per_page}'  class='{css}'>&gt;&gt;</a>"
    return out + f'<br>[viso:&nbsp;{count}]'
