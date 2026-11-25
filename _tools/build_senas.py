#!/usr/bin/env python3
import argparse
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl

from php_port import Template, hs, nl2br, paging, parse_dump

DATA = Path('foje.lt/foje.lt/data')
DUMP = 'foje1.sql'

SPAM_FORUM_POSTS = {5305, 5328, 5350, 8167, 8211}
SPAM_MARKERS = re.compile(r'https?://|www\.|\[url|\[a\]|<a |</a>|\.com|\.info|\.biz|Content-Type:', re.I)
LITHUANIAN = re.compile(
    r'[ąčęėįšųūž]|\b(ir|kad|labai|foje|daina|dainos|dainu|dainas|albumas|albuma|tai|yra|geras|mamontov\w*|aciu|ačiū'
    r'|grupe|grupė|muzika|nereal\w*|juos|maciau)\b',
    re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
# Spam bots found the album comments in 2007; earlier comments without links all come from fans.
BOT_ERA = '2007'

DEAD = '#dead'
DEAD_URLS = re.compile(
    r'www/chat/chat\.html|http://(?:andrius\.m\.tdd\.lt|am\.area\.lt|www\.vrs\.lt|www\.MBT\.lt|localhost)[^"\'\s<>]*', re.I)
# The backup lacks the 2006 album cover; the current site has the same cover. Values: (file, thumbnail size or 0).
MISSING_ASSETS = {
    'images/gallery/foje_kpp.jpg': ('imgs/FOJE_virselis_kita_paveikslo_puse_cd.jpg', 0),
    'images/gallery/small/foje_kpp.jpg': ('imgs/FOJE_virselis_kita_paveikslo_puse_cd.jpg', 100),
}
CIRCUIT_ALIASES = {'forums': 'forum'}
OFFSET_PARAMS = {'paging', 'page'}
REPO = Path(__file__).resolve().parent.parent
TABLES = {'discography_albums', 'discography_tracks', 'gallery', 'gallery_categories', 'facts', 'media',
          'album_comments', 'forum_posts'}
# Rows the 2011 cleanup truncated. All were read by hand: 2007 bot comments and two pasted Wikipedia
# articles in the forum. Any other count means the parser lost real rows.
EXPECTED_BROKEN_ROWS = {'album_comments': 654, 'forum_posts': 2}
COMMENTS_PER_PAGE = 10
ARTICLES_PER_PAGE = 5
PHOTO_COLUMNS = 3
PHOTOS_PER_PAGE = PHOTO_COLUMNS * 3
THREADS_PER_PAGE = 30


def is_fan_comment(row):
    text = EMAIL.sub('', row['comment'])
    return not SPAM_MARKERS.search(text) and (row['date'][:4] < BOT_ERA or bool(LITHUANIAN.search(text)))


class Archive:
    def __init__(self, old_root):
        self.www = old_root / DATA / 'www'
        db, skipped = parse_dump(old_root / DUMP, TABLES)
        self.broken_rows = {table: n for table, n in skipped.items() if n}
        if self.broken_rows != EXPECTED_BROKEN_ROWS:
            raise SystemExit(f'unparsed dump rows {self.broken_rows}, expected {EXPECTED_BROKEN_ROWS}')
        self.albums = [a for a in db['discography_albums'] if a['deleted'] == 0]
        self.tracks = [t for t in db['discography_tracks'] if t['deleted'] == 0]
        self.gallery = db['gallery']
        self.gallery_by_id = {str(g['id']): g for g in self.gallery}
        self.gallery_by_name = {g['name']: g for g in self.gallery}
        self.categories = db['gallery_categories']
        self.facts = [f for f in db['facts'] if f['deleted'] == 0]
        self.media = [m for m in db['media'] if m['deleted'] == 0]
        self.comments = [c for c in db['album_comments'] if is_fan_comment(c)]
        threads = {p['id'] for p in db['forum_posts'] if p['parent_id'] == 0 and p['id'] not in SPAM_FORUM_POSTS}
        self.posts = [p for p in db['forum_posts']
                      if p['id'] not in SPAM_FORUM_POSTS and (p['parent_id'] == 0 or p['parent_id'] in threads)]
        self.dropped_comments = len(db['album_comments']) - len(self.comments)

    def template(self, relative):
        return Template((self.www / '_code' / relative).read_bytes().decode('cp1257'))

    def layout(self, relative, inner):
        page = self.template(relative)
        page.set(layout=inner)
        return page.render().strip()


def mysql_date(value):
    return value[:10].replace('-', '.')


def next_color(color):
    return '#6F6F6F' if color == '#999999' else '#999999'


def forum_time(stamp, sep):
    return f'{stamp[0:4]}.{stamp[4:6]}.{stamp[6:8]}{sep}{stamp[8:10]}:{stamp[10:12]}'


def render_main(a, params):
    return a.layout('lay_main.html', a.template('dsp_main.html').render())


def render_bio(a, params):
    page_id = params.get('id', '') if params['action'] == 'bio' else ''
    name = f'dsp_bio{page_id}.html' if page_id in {'1', '2', '3', '4'} else 'dsp_browse.html'
    inner = a.layout('chrestomatija/bio/lay_main.html', a.template(f'chrestomatija/bio/{name}').render())
    return a.layout('lay_main.html', inner)


def comment_count(a, album_id):
    return sum(1 for c in a.comments if c['album'] == album_id)


def render_disco_browse(a, params):
    t = a.template('chrestomatija/disco/dsp_browse.html')
    t.block('next_block', 'next_b')
    t.block('album_block', 'album')
    albums = [x for x in a.albums if x['photo'] in a.gallery_by_id]
    color = ''
    for album in sorted(albums, key=lambda x: x['year'], reverse=True):
        color = next_color(color)
        t.set(album_id=album['id'])
        description = album['description']
        if len(description) > 150:
            t.set(text=hs(description[:150]))
            t.parse('next_b', 'next_block')
        else:
            t.set(text=hs(description), next_b='')
        t.set(image=a.gallery_by_id[album['photo']]['name'], album_name=hs(album['name']),
              album_year=album['year'], color=color)
        t.set(comment_count=comment_count(a, album['id']))
        t.parse('album', 'album_block', True)
    return a.layout('lay_main.html', t.render())


def album(a, album_id):
    return next((x for x in a.albums if str(x['id']) == album_id), None)


def render_disco_tracks(a, params):
    record = album(a, params.get('id'))
    if not record:
        return None
    t = a.template('chrestomatija/disco/dsp_tracks.html')
    for handle in ('text', 'audio', 'video', 'no_text', 'no_audio', 'no_video', 'album', 'track'):
        t.block(f'{handle}_block', handle)
    photo = a.gallery_by_id.get(record['photo'], {})
    color = '#999999'
    t.set(width=photo.get('width', ''), height=photo.get('height', ''), image=photo.get('name', ''),
          album_id=record['id'], album_name=hs(record['name']), album_year=record['year'],
          text=nl2br(hs(record['description'])), color=color, comment_count=comment_count(a, record['id']))
    t.parse('album', 'album_block')
    tracks = [x for x in a.tracks if x['album_id'] == record['id']]
    for number, track in enumerate(tracks, 1):
        color = next_color(color)
        audio, video = ('' if DEAD_URLS.match(link or '') else (link or '').replace('http://foje.lt/www/', '')
                        for link in (track['audio'], track['video']))
        t.set(track_no=number, track_name=track['name'], track_id=track['id'], text='',
              audio_link=audio, video_link=video, color=color)
        for handle, present in (('text', track['text']), ('audio', audio), ('video', video)):
            if present:
                t.set(**{f'no_{handle}': ''})
                t.parse(handle, f'{handle}_block')
            else:
                t.set(**{handle: ''})
                t.parse(f'no_{handle}', f'no_{handle}_block')
        t.parse('track', 'track_block', True)
    return a.layout('lay_main.html', t.render())


def render_disco_text(a, params):
    track = next((x for x in a.tracks if str(x['id']) == params.get('id')), None)
    if not track:
        return None
    record = album(a, str(track['album_id'])) or {}
    t = a.template('chrestomatija/disco/dsp_text.html')
    t.block('text_block', 'text')
    t.set(txt=nl2br(track['text']), track_name=track['name'], album_name=record.get('name', ''),
          album_id=track['album_id'])
    t.parse('text', 'text_block', True)
    return a.layout('lay_main.html', t.render())


def render_disco_comments(a, params):
    record = album(a, params.get('id'))
    if not record:
        return None
    offset = int(params.get('paging', 0))
    t = a.template('chrestomatija/disco/dsp_comments.html')
    t.block('post_block', 'post')
    t.set(album_name=record['name'])
    comments = sorted((c for c in a.comments if c['album'] == record['id']),
                      key=lambda c: (c['date'], c['id']), reverse=True)
    shown = comments[offset:offset + COMMENTS_PER_PAGE]
    t.set(message_count=len(shown), album_id=record['id'])
    for c in shown:
        d = c['date']
        t.set(album_id=record['id'], post_topic='', post_author=hs(c['nick']), post_author_email=hs(c['email']),
              post_message=nl2br(hs(c['comment'])),
              post_time=f'{mysql_date(d)} - {d[11:16]}')
        t.parse('post', 'post_block', True)
    t.set(paging=paging(offset, len(comments), COMMENTS_PER_PAGE, '',
                        f"index.php?fuseaction=disco.view_comments&id={record['id']}&paging="))
    return a.layout('lay_main.html', t.render())


def article_rows(a, rows):
    return sorted(rows, key=lambda x: x['date'], reverse=True)


def set_article_image(t, a, row):
    photo = a.gallery_by_name.get(row['image'], {}) if row['image'] else {}
    t.set(image=row['image'] or '', width=photo.get('width', ''), height=photo.get('height', ''))


def render_articles(a, params, circuit, rows):
    offset = int(params.get('paging', 0))
    t = a.template(f'chrestomatija/{circuit}/dsp_browse.html')
    for handle, name in (('img_block', 'img'), ('more_block', 'more_b'), ('all_facts_block', 'all_facts'),
                         ('pages_block', 'pages')):
        t.block(handle, name)
    for i, row in enumerate(article_rows(a, rows)[offset:offset + ARTICLES_PER_PAGE], 1):
        t.set(bg_color=('#9DAEB8', '#AEB5BA')[i % 2])
        set_article_image(t, a, row)
        if row['image']:
            t.parse('img', 'img_block')
        else:
            t.set(img='')
        body = nl2br(hs(row['body']))
        if len(body) > 100:
            body = body[:100] + '&lt;..&gt;'
            t.set(id=row['id'])
            t.parse('more_b', 'more_block')
        else:
            t.set(more_b='')
        t.set(name=hs(row['name']), date=mysql_date(row['date']), body=body)
        t.parse('all_facts', 'all_facts_block', True)
    t.set(paging=paging(offset, len(rows), ARTICLES_PER_PAGE, '', f'index.php?fuseaction={circuit}.browse&sort=&paging='))
    if len(rows) > ARTICLES_PER_PAGE:
        t.parse('pages', 'pages_block', True)
    else:
        t.set(pages='')
    return a.layout('lay_main.html', t.render())


def render_article(a, params, circuit, rows):
    row = next((x for x in rows if str(x['id']) == params.get('id')), None)
    if not row:
        return None
    t = a.template(f'chrestomatija/{circuit}/dsp_item.html')
    t.block('img_block', 'img')
    t.set(turbo='', name=hs(row['name']), date=mysql_date(row['date']), body=nl2br(hs(row['body'])))
    if row['image']:
        t.set(image=row['image'])
        t.parse('img', 'img_block')
    set_article_image(t, a, row)
    return a.layout('lay_main.html', t.render())


def render_gallery(a, params):
    t = a.template('gallery/dsp_browse.html')
    t.block('list_block', 'list_b')
    for category in sorted((c for c in a.categories if c['deleted'] == 0), key=lambda c: c['name'].lower()):
        count = sum(1 for g in a.gallery if g['deleted'] == 0 and g['category_id'] == category['id'])
        t.set(id=category['id'], name=hs(category['name']), count=count)
        t.parse('list_b', 'list_block', True)
    return a.layout('lay_main.html', t.render())


def render_gallery_photos(a, params):
    category = params.get('id')
    offset = int(params.get('paging', 0))
    t = a.template('gallery/dsp_browse_foto.html')
    t.block('list_cols_block', 'list_cols_b')
    t.block('list_rows_block', 'list_rows_b')
    photos = sorted((g for g in a.gallery if g['deleted'] == 0 and str(g['category_id']) == category),
                    key=lambda g: g['id'], reverse=True)
    for i, photo in enumerate(photos[offset:offset + PHOTOS_PER_PAGE], 1):
        t.set(id=photo['id'], name=photo['name'], width=photo['width'], height=photo['height'],
              comment=hs(photo['comment']))
        t.parse('list_cols_b', 'list_cols_block', True)
        if i % PHOTO_COLUMNS == 0:
            t.parse('list_rows_b', 'list_rows_block', True)
            t.set(list_cols_b='')
    t.parse('list_rows_b', 'list_rows_block', True)
    t.set(paging=paging(offset, len(photos), PHOTOS_PER_PAGE, '', f'index.php?fuseaction=gallery.browseFoto&id={category}&paging=')
          if len(photos) > PHOTOS_PER_PAGE else '')
    return a.layout('lay_main.html', t.render())


def render_links(a, params):
    return a.layout('lay_main.html', a.template('links/dsp_browse.html').render())


def set_author(t, email, prefix):
    if email:
        t.set(**{f'{prefix}_author_email': hs(email), 'no_mail': ''})
        t.parse('mail', 'mail_block')
    else:
        t.set(**{f'{prefix}_author_email': '', 'mail': ''})
        t.parse('no_mail', 'no_mail_block')


def render_forum(a, params):
    offset = int(params.get('page', 0))
    t = a.template('forum/dsp_threads.html')
    t.text = re.sub(r'<div align="right"><a href="index\.php\?fuseaction=forum\.newTopic".*?</div>', '', t.text,
                    flags=re.S)
    for handle, name in (('paging_block', 'paging'), ('mail_block', 'mail'), ('no_mail_block', 'no_mail'),
                         ('thread_block', 'thread')):
        t.block(handle, name)
    threads = sorted((p for p in a.posts if p['parent_id'] == 0), key=lambda p: p['last_message_time'], reverse=True)
    for thread in threads[offset:offset + THREADS_PER_PAGE]:
        replies = sum(1 for p in a.posts if p['parent_id'] == thread['id'])
        t.set(thread_id=thread['id'], thread_mood=thread['mood'] if 1 <= thread['mood'] <= 14 else 0,
              thread_topic=hs(thread['topic']), thread_author=hs(thread['nick']),
              thread_message_count=replies + 1, thread_visit_count=thread['visits'],
              thread_last_message=forum_time(thread['last_message_time'], '&nbsp;'))
        set_author(t, thread['email'], 'thread')
        t.parse('thread', 'thread_block', True)
    if len(threads) > THREADS_PER_PAGE:
        t.set(topic_counter=paging(offset, len(threads), THREADS_PER_PAGE, '', 'index.php?fuseaction=forum.browse&page='))
        t.parse('paging', 'paging_block')
    return a.layout('lay_popup.html', t.render())


def render_thread(a, params):
    thread = next((p for p in a.posts if str(p['id']) == params.get('id') and p['parent_id'] == 0), None)
    if not thread:
        return None
    t = a.template('forum/dsp_thread.html')
    for handle, name in (('no_mail_block', 'no_mail'), ('mail_block', 'mail'), ('post_block', 'post')):
        t.block(handle, name)
    posts = sorted((p for p in a.posts if p['id'] == thread['id'] or p['parent_id'] == thread['id']),
                   key=lambda p: (p['time'], p['id']))
    for number, post in enumerate(posts, 1):
        if post['id'] == thread['id']:
            t.set(id=thread['id'], topic=hs(thread['topic']), message_count=len(posts), visit_count=thread['visits'])
        t.set(post_id=post['id'], post_no=number, post_mood=post['mood'], post_topic=hs(post['topic']),
              post_author=hs(post['nick']), post_message=nl2br(hs(post['message'])),
              post_time=forum_time(post['time'], ' '))
        set_author(t, post['email'], 'post')
        t.parse('post', 'post_block', True)
    return a.layout('lay_popup.html', t.render())


ROUTES = {
    'main.browse': render_main,
    'bio.browse': render_bio,
    'bio.bio': render_bio,
    'disco.browse': render_disco_browse,
    'disco.view_tracks': render_disco_tracks,
    'disco.show_text': render_disco_text,
    'disco.view_comments': render_disco_comments,
    'facts.browse': lambda a, p: render_articles(a, p, 'facts', a.facts),
    'facts.displayItem': lambda a, p: render_article(a, p, 'facts', a.facts),
    'media.browse': lambda a, p: render_articles(a, p, 'media', a.media),
    'media.displayItem': lambda a, p: render_article(a, p, 'media', a.media),
    'gallery.browse': render_gallery,
    'gallery.browseFoto': render_gallery_photos,
    'links.browse': render_links,
    'forum.browse': render_forum,
    'forum.viewThread': render_thread,
}


def page_key(query):
    params = dict(parse_qsl((query or '').lstrip('?').replace('&amp;', '&'), keep_blank_values=True))
    circuit, _, action = params.pop('fuseaction', 'main.browse').partition('.')
    circuit = CIRCUIT_ALIASES.get(circuit, circuit)
    action = action or 'browse'
    kept = tuple(sorted((k, v) for k, v in params.items()
                        if v != '' and not (k in OFFSET_PARAMS and v == '0') and k != 'sort'))
    return f'{circuit}.{action}', kept


def file_name(key):
    route, params = key
    if route == 'main.browse' and not params:
        return 'index.html'
    return '.'.join([route] + [f'{k}-{v}' for k, v in params]) + '.html'


LINK = re.compile(r'((?:www/)?)index\.php(\?[^"\'\s>]*)?')
COMMENTED_LINK = re.compile(r'<!--(?:(?!-->).)*index\.php(?:(?!-->).)*-->', re.S)


def links(page):
    return [page_key(m[2]) for m in LINK.finditer(page)]


def finish(page, pages):
    def target(m):
        key = page_key(m[2])
        return m[1] + file_name(key) if pages.get(key) else DEAD

    page = LINK.sub(target, page)
    page = re.sub(r'(["\'])(?:%s)' % DEAD_URLS.pattern, r'\1' + DEAD, page, flags=re.I)
    page = page.replace('_code/gallery/showfoto.php?img=', 'images/gallery/')
    page = re.sub(r'<a\b[^>]*%s[^>]*>(.*?)</a>' % DEAD, r'\1', page, flags=re.S | re.I)
    page = re.sub(r'<area\b[^>]*%s[^>]*>\s*' % DEAD, '', page, flags=re.I)
    if DEAD in page:
        raise SystemExit(f'unresolved dead link left in page: ...{page[max(0, page.index(DEAD) - 200):][:300]}')
    return re.sub(r'charset=windows-1257', 'charset=utf-8', page, flags=re.I)


def prepare(page):
    page = COMMENTED_LINK.sub('', page)
    return re.sub(r'<form\b.*?</form>', '', page, flags=re.S | re.I)


ASSET = re.compile(r'(?:www/)?((?:images|video)/[^"\'\s)<>]+\.(?:gif|jpe?g|png|mpg))', re.I)


def asset_source(a, asset):
    if (a.www / asset).is_file():
        return a.www / asset, 0
    if asset in MISSING_ASSETS:
        replacement, thumbnail = MISSING_ASSETS[asset]
        return REPO / replacement, thumbnail
    return None


def build(old_root, out):
    a = Archive(old_root)
    front = prepare((old_root / DATA / 'index.html').read_bytes().decode('cp1257'))
    pages = {}
    queue = deque(links(front))
    while queue:
        key = queue.popleft()
        if key in pages:
            continue
        render = ROUTES.get(key[0])
        html_page = render(a, {'action': key[0].split('.')[1], **dict(key[1])}) if render else None
        pages[key] = prepare(html_page) if html_page else None
        if html_page:
            queue.extend(links(pages[key]))

    finals = {file_name(key): finish(page, pages) for key, page in pages.items() if page}
    front_page = finish(front, pages)
    css = (a.www / 'style.css').read_bytes().decode('cp1257')
    assets = set(ASSET.findall(css + front_page)).union(*(ASSET.findall(page) for page in finals.values()))
    sources = {asset: asset_source(a, asset) for asset in sorted(assets)}
    missing = [asset for asset, source in sources.items() if not source]
    if missing:
        raise SystemExit(f'missing assets: {missing}')

    if out.exists():
        shutil.rmtree(out)
    (out / 'www').mkdir(parents=True)
    (out / 'index.html').write_text(front_page, encoding='utf-8')
    (out / 'www' / 'style.css').write_text(css, encoding='utf-8')
    for name, page in finals.items():
        (out / 'www' / name).write_text(page, encoding='utf-8')
    for asset, (source, thumbnail) in sources.items():
        target = out / 'www' / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        if thumbnail:
            subprocess.run(['sips', '-Z', str(thumbnail), str(source), '--out', str(target)],
                           check=True, stdout=subprocess.DEVNULL)
        else:
            shutil.copy2(source, target)

    print(f'pages: {len(finals)} written; unlinked: {sorted(file_name(k) for k, p in pages.items() if not p)}')
    print(f'assets: {len(sources)} copied')
    print(f'album comments: {len(a.comments)} kept, {a.dropped_comments} dropped as spam')
    print(f'forum posts: {len(a.posts)} kept; broken rows skipped: {a.broken_rows}')


def main():
    parser = argparse.ArgumentParser(description='Rebuild the 2002-2007 foje.lt as static pages under senas/.')
    parser.add_argument('old_root', type=Path, help='path to the FOJE_old_version folder')
    parser.add_argument('--out', type=Path, default=REPO / 'senas')
    args = parser.parse_args()
    build(args.old_root, args.out)


if __name__ == '__main__':
    main()
