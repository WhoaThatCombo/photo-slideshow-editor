"""Photo Slideshow Editor - one local web app for every slideshow project.

Start it with "Start Photo Editor.bat" (or: python _photo_editor.py). It opens in
your browser. Each project is a folder holding project/photo_order.txt, its photos,
its songs and its finished videos; projects.json lists them. Nothing is uploaded
anywhere - it only talks to this computer and devices on the home wi-fi.
"""
import os, re, io, json, shutil, socket, threading, subprocess, webbrowser, sys, time
import datetime, unicodedata
import http.server, socketserver, urllib.parse
from PIL import Image, ImageOps

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import _settings as SETTINGS
import _build_video as B

PROJECTS_FILE = os.path.join(APP, 'projects.json')
DEFAULT_PORT = 8778
THUMB_W = 400
AUDIO_TYPES = {'.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
               '.opus': 'audio/ogg', '.ogg': 'audio/ogg', '.wav': 'audio/wav',
               '.flac': 'audio/flac', '.wma': 'audio/x-ms-wma'}
MAX_SONG = 300 * 1024 * 1024
NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

state = {'log': [], 'running': False, 'project': None}


# ---------------------------------------------------------------- projects
class Project:
    def __init__(self, d):
        self.id, self.name, self.path = d['id'], d['name'], os.path.abspath(d['path'])
        self.port = d.get('port')
        self.src = os.path.join(self.path, 'project')
        self.order = os.path.join(self.src, 'photo_order.txt')
        self.work = os.path.join(self.path, '_workfiles')
        self.thumbs = os.path.join(self.work, 'thumbs')
        self.history = os.path.join(self.work, 'order_history')
        self.songs_file = os.path.join(self.path, B.SONGS_FILE)

    def as_json(self):
        return {'id': self.id, 'name': self.name, 'path': self.path, 'port': self.port}


REG_LOCK = threading.Lock()


def load_projects():
    try:
        data = json.load(open(PROJECTS_FILE, encoding='utf-8'))
    except Exception:
        data = {}
    global PROJECTS_DIR
    # where "New project" makes its folders (projects.json can point it elsewhere)
    PROJECTS_DIR = data.get('projects_dir') or os.path.join(APP, 'projects')
    out = []
    for d in data.get('projects', []):
        if os.path.isfile(os.path.join(d.get('path', ''), 'project', 'photo_order.txt')):
            out.append(Project(d))
        else:
            print(f'  (skipping project "{d.get("name")}" - its folder is missing)')
    return out


PROJECTS = load_projects()


def save_projects():
    with REG_LOCK:
        tmp = PROJECTS_FILE + '.tmp'
        json.dump({'projects_dir': PROJECTS_DIR, 'projects': [p.as_json() for p in PROJECTS]},
                  open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, PROJECTS_FILE)


def get_project(pid, port=None):
    for p in PROJECTS:
        if p.id == pid:
            return p
    for p in PROJECTS:
        if port and p.port == port:
            return p
    return PROJECTS[0] if PROJECTS else None


def slug(s):
    s = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', s).encode('ascii', 'ignore')
               .decode().lower()).strip('-')[:30] or 'project'
    base, k = s, 2
    while any(p.id == s for p in PROJECTS):
        s = f'{base}-{k}'
        k += 1
    return s


HEADER = """# PHOTO ORDER - managed by the Photo Editor.
# You can edit by hand too: one slide per line, <number> | <date> | <filename>.
# Lines starting with # are skipped (that is how a removed photo is stored).
"""


def new_project(name):
    name = re.sub(r'\s+', ' ', str(name or '')).strip()[:60] or 'New slideshow'
    folder_name = SETTINGS.clean_name(name, 'New slideshow')
    parent = PROJECTS_DIR
    os.makedirs(parent, exist_ok=True)
    folder, k = os.path.join(parent, folder_name), 2
    while os.path.exists(folder):
        folder = os.path.join(parent, f'{folder_name} ({k})')
        k += 1
    os.makedirs(os.path.join(folder, 'project'))
    open(os.path.join(folder, 'project', 'photo_order.txt'), 'w', encoding='utf-8').write(HEADER)
    SETTINGS.save(folder, {'name': folder_name})
    p = Project({'id': slug(name), 'name': name, 'path': folder})
    PROJECTS.append(p)
    save_projects()
    return p


def project_summary(p):
    n = 0
    try:
        for l in open(p.order, encoding='utf-8'):
            if B.LINE.match(l.strip()) and not l.strip().startswith('#'):
                n += 1
    except OSError:
        pass
    s = SETTINGS.load(p.path)
    return {'id': p.id, 'name': p.name, 'photos': n,
            'music': bool(s['music'] and B.song_names(p.path)),
            'video': SETTINGS.outname(s), 'folder': os.path.basename(p.path)}


# ---------------------------------------------------------------- order file
def read_order(P):
    entries = []
    for raw in open(P.order, encoding='utf-8'):
        s = raw.strip()
        if not s:
            continue
        removed = False
        if s.startswith('#'):
            body = s.lstrip('#').strip()
            if not B.LINE.match(body):
                continue                      # a real comment, not a removed photo
            s, removed = body, True
        m = B.LINE.match(s)
        if not m:
            continue
        f = m.group(3)
        fixed, rot = B.extras(m)
        entries.append({'date': m.group(2), 'file': f, 'removed': removed,
                        'fixed': fixed, 'rot': rot,
                        'exists': os.path.exists(os.path.join(P.src, f))})
    return entries


def snapshot_order(P, tag='save'):
    """Keep a timestamped copy of every version. A single .bak is not enough:
    one bad edit followed by one more save would overwrite the only good copy."""
    if not os.path.exists(P.order):
        return None
    os.makedirs(P.history, exist_ok=True)
    name = time.strftime('%Y-%m-%d_%H%M%S') + f'_{tag}.txt'
    dst = os.path.join(P.history, name)
    if not os.path.exists(dst):
        shutil.copy2(P.order, dst)
    for old in sorted(os.listdir(P.history))[:-60]:      # keep the last 60 versions
        try:
            os.remove(os.path.join(P.history, old))
        except OSError:
            pass
    return name


def history_list(P):
    if not os.path.isdir(P.history):
        return []
    out = []
    for f in sorted(os.listdir(P.history), reverse=True):
        p = os.path.join(P.history, f)
        try:
            n = sum(1 for l in open(p, encoding='utf-8')
                    if B.LINE.match(l.strip()) and not l.strip().startswith('#'))
        except Exception:
            n = 0
        out.append({'name': f, 'slides': n,
                    'when': time.strftime('%a %d %b, %H:%M', time.localtime(os.path.getmtime(p)))})
    return out[:60]


def write_order(P, entries):
    snapshot_order(P, 'before')
    if os.path.exists(P.order):
        shutil.copy2(P.order, P.order + '.bak')
    out = [HEADER]
    n = 0
    for e in entries:
        # keep any pinned length and rotation, or they would be lost on the next save
        tail = f" | {e['fixed']:g}s" if e.get('fixed') else ''
        if e.get('rot'):
            tail += f" | rot{e['rot']}"
        if e['removed']:
            out.append(f"# {0:>4} | {e['date']} | {e['file']}{tail}\n")
        else:
            n += 1
            out.append(f"{n:>4} | {e['date']} | {e['file']}{tail}\n")
    open(P.order, 'w', encoding='utf-8').writelines(out)
    return n


def built_info(P):
    p = os.path.join(P.path, '_built_order.json')
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            pass
    return None


def videos(P):
    out = []
    for f in os.listdir(P.path):
        if f.lower().endswith('.mp4'):
            try:
                st = os.stat(os.path.join(P.path, f))
            except OSError:
                continue
            out.append({'name': f, 'mb': round(st.st_size / 1048576), 't': st.st_mtime,
                        'when': time.strftime('%d %b, %H:%M', time.localtime(st.st_mtime))})
    out.sort(key=lambda v: -v['t'])
    return out


def rename_videos(P, old, new):
    """When the video name changes, rename the files already built so the
    name takes effect straight away instead of at the next build."""
    renamed, note = [], ''
    if state['running'] and state['project'] == P.id:
        return renamed, 'A video is being built right now - the new name is used from the next build.'
    info = built_info(P)
    for numbered in (False, True):
        a = os.path.join(P.path, SETTINGS.outname(old, numbered))
        b = os.path.join(P.path, SETTINGS.outname(new, numbered))
        if not os.path.exists(a):
            continue
        same = os.path.normcase(a) == os.path.normcase(b)     # only the capitals changed
        if os.path.exists(b) and not same:
            note = f'There is already a video called "{os.path.basename(b)}" - it will be replaced at the next build.'
            continue
        try:
            os.rename(a, b)
            renamed.append(os.path.basename(b))
            if info and info.get('video') == os.path.basename(a):
                info['video'] = os.path.basename(b)
                json.dump(info, open(os.path.join(P.path, '_built_order.json'), 'w'), indent=1)
        except OSError:
            note = ('The old video could not be renamed (it may be playing somewhere) - '
                    'the new name is used from the next build.')
    return renamed, note


# ---------------------------------------------------------------- photos
UPLOAD_EXT = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
KEEP_AS_IS = {'.jpg', '.jpeg', '.png'}
MAX_UPLOAD = 80 * 1024 * 1024

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

DT_RE = re.compile(r'(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})')


def exif_date(path):
    """Capture date as YYYY-MM-DD, or None. Reads EXIF, then falls back to
    scanning for an embedded Exif block (how HEIC stores it)."""
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            for tag in (36867, 36868, 306):
                m = DT_RE.match(str(ex.get(tag) or '').strip())
                if m:
                    return '%s-%s-%s' % m.groups()[:3]
            try:
                sub = ex.get_ifd(0x8769)
                for tag in (36867, 36868):
                    m = DT_RE.match(str(sub.get(tag) or '').strip())
                    if m:
                        return '%s-%s-%s' % m.groups()[:3]
            except Exception:
                pass
    except Exception:
        pass
    try:
        with open(path, 'rb') as f:
            blob = f.read(3 * 1024 * 1024)
        for m in DT_RE.finditer(blob.decode('latin-1', 'ignore')):
            y = int(m.group(1))
            if 1900 < y <= datetime.date.today().year:
                return '%s-%s-%s' % m.groups()[:3]
    except Exception:
        pass
    return None


def safe_name(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^A-Za-z0-9._ ()-]', '_', s).strip()
    return s[:70] or 'photo'


def store_upload(P, raw, filename):
    """Save an uploaded image into the project folder, upright and web-safe.
    Returns (stored_name, date_or_None)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in UPLOAD_EXT:
        raise ValueError(f'{ext or "that file type"} is not a photo')
    if len(raw) > MAX_UPLOAD:
        raise ValueError('file is larger than 80 MB')
    os.makedirs(P.work, exist_ok=True)
    tmp = os.path.join(P.work, '_incoming' + ext)
    with open(tmp, 'wb') as f:
        f.write(raw)
    with Image.open(tmp) as probe:      # rejects anything that is not really an image
        probe.verify()
    date = exif_date(tmp)
    stem = safe_name(os.path.splitext(os.path.basename(filename))[0])
    out_ext = ext if ext in KEEP_AS_IS else '.jpg'
    base = f'NEW_{date or "nodate"}_{stem}'
    dst = os.path.join(P.src, base + out_ext)
    k = 2
    while os.path.exists(dst):
        dst = os.path.join(P.src, f'{base}-{k}{out_ext}')
        k += 1
    with Image.open(tmp) as im:
        exif = im.info.get('exif')
        im = ImageOps.exif_transpose(im)       # bake rotation in
        if out_ext == '.png':
            im.save(dst, 'PNG', optimize=True)
        else:
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            kw = dict(quality=94, subsampling=0, optimize=True)
            if exif:
                kw['exif'] = exif
            im.save(dst, 'JPEG', **kw)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return os.path.basename(dst), date


def parsed_date(e):
    d = (e.get('date') or '').lstrip('~').strip()
    return d if re.match(r'^\d{4}-\d{2}-\d{2}$', d) else None


def mostly_chronological(entries):
    ds = [d for d in (parsed_date(e) for e in entries) if d]
    if len(ds) < 8:
        return False
    ok = sum(1 for a, b in zip(ds, ds[1:]) if a <= b)
    return ok / max(1, len(ds) - 1) > 0.9


def insert_new(entries, new_entry):
    """Chronological insertion only while the running order IS chronological;
    once rearranged by hand, append to the end where it is easy to find."""
    nd = parsed_date(new_entry)
    if nd and mostly_chronological(entries):
        idx = None
        for i, e in enumerate(entries):
            d = parsed_date(e)
            if d and d <= nd:
                idx = i
        pos = (idx + 1) if idx is not None else (1 if entries and entries[0].get('fixed') else 0)
        entries.insert(pos, new_entry)
        return pos + 1, 'by date'
    entries.append(new_entry)
    return len(entries), 'at the end'


def make_thumb(P, name):
    p = os.path.join(P.thumbs, re.sub(r'[^A-Za-z0-9._-]', '_', name) + '.jpg')
    if os.path.exists(p):
        return p
    src = os.path.join(P.src, name)
    if not os.path.exists(src):
        return None
    os.makedirs(P.thumbs, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert('RGB')
        im.thumbnail((THUMB_W, THUMB_W), Image.LANCZOS)
        im.save(p, 'JPEG', quality=82, optimize=True)
    return p


def prebuild_thumbs():
    for P in list(PROJECTS):
        try:
            for e in read_order(P):
                try:
                    make_thumb(P, e['file'])
                except Exception:
                    pass
        except Exception:
            pass
    print('  (thumbnails ready)', flush=True)


def preview_jpeg(P, kind, name=None, rot=None):
    """One slide drawn exactly as the builder will draw it, with the saved settings."""
    L = B.Look(SETTINGS.load(P.path))
    ents = read_order(P)
    live = [e for e in ents if not e['removed'] and e['exists']]
    if kind == 'photo':
        e = next((x for x in ents if x['file'] == name and x['exists']), None)
        if not e:
            return None
        r = e['rot'] if rot is None else rot
        img = B.compose_photo(B.open_photo(P.src, name, r), L, e['date'])
    else:
        s = L.s
        lines = [s['title'], s['subtitle']] if kind == 'title' else [s['ending'], '']
        first = B.open_photo(P.src, live[0]['file'], live[0]['rot']) if live else None
        img = B.text_card(lines, L, first)
    img.thumbnail((1600, 900), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=88)
    return buf.getvalue()


# ---------------------------------------------------------------- songs
FFPROBE = None
_dur_cache = {}


def audio_seconds(path):
    global FFPROBE
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, int(st.st_mtime))
    if key in _dur_cache:
        return _dur_cache[key]
    if FFPROBE is None:
        ff = B.find_ffmpeg()
        FFPROBE = os.path.join(os.path.dirname(ff), 'ffprobe.exe')
        if not os.path.exists(FFPROBE):
            FFPROBE = shutil.which('ffprobe') or 'ffprobe'
    try:
        out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'csv=p=0', path], capture_output=True, text=True,
                             timeout=30, creationflags=NOWIN).stdout.strip()
        d = round(float(out), 2)
    except Exception:
        d = None
    _dur_cache[key] = d
    return d


def songs_state(P):
    order = B.song_names(P.path)
    return {
        'songs': [{'name': n, 'dur': audio_seconds(os.path.join(P.path, n))} for n in order],
        'unused': [{'name': n, 'dur': audio_seconds(os.path.join(P.path, n))}
                   for n in B.audio_files(P.path) if n not in order],
    }


SONG_HEADER = """# SONGS - the order they play in the video. One file name per line.
# Managed by the Photo Editor's Music tab. Lines starting with # are ignored.

"""


def write_songs(P, names):
    ok = set(B.audio_files(P.path))
    keep = []
    for n in names:
        n = os.path.basename(str(n))
        if n in ok and n not in keep:
            keep.append(n)
    tmp = P.songs_file + '.tmp'
    open(tmp, 'w', encoding='utf-8').write(SONG_HEADER + ''.join(n + '\n' for n in keep))
    os.replace(tmp, P.songs_file)
    return keep


def store_song(P, raw, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in B.AUDIO_EXT:
        raise ValueError(f'{ext or "that file type"} is not a song file (mp3, m4a, wav...)')
    if len(raw) > MAX_SONG:
        raise ValueError('file is larger than 300 MB')
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', os.path.splitext(os.path.basename(filename))[0])
    stem = stem.strip().lstrip('._') [:90] or 'song'
    dst, k = os.path.join(P.path, stem + ext), 2
    while os.path.exists(dst):
        dst = os.path.join(P.path, f'{stem} ({k}){ext}')
        k += 1
    with open(dst + '.part', 'wb') as f:
        f.write(raw)
    os.replace(dst + '.part', dst)
    if audio_seconds(dst) is None:
        os.remove(dst)
        raise ValueError('could not read it as audio')
    return os.path.basename(dst)


# ---------------------------------------------------------------- build
RE_RENDER = re.compile(r'^rendered (\d+)/(\d+)')
RE_TOTAL = re.compile(r'slides x [\d.]+s -> ([\d.]+)s')
RE_FRAME = re.compile(r'^frame=\s*(\d+).*?speed=\s*([\d.]+)x')


def build_progress(log, running):
    """Turn the builder's chatter into one monotonic 0-100 bar. Rendering and
    encoding are weighted into a single bar - otherwise it would fill up, reset,
    and fill again, which looks like a restart."""
    phase, sub, detail, eta, total_frames = 'starting', 0.0, 'starting up', None, None
    for l in log:
        if l.strip().startswith('mixing soundtrack'):
            phase, sub, detail = 'mixing', 0.0, 'mixing the songs together'
        elif l.strip().startswith('soundtrack unchanged'):
            detail = 'music ready'
        m = RE_RENDER.match(l)
        if m:
            phase, sub = 'rendering', 100.0 * int(m.group(1)) / max(1, int(m.group(2)))
            detail = f'preparing photo {m.group(1)} of {m.group(2)}'
        m = RE_TOTAL.search(l)
        if m:
            total_frames = float(m.group(1)) * B.FPS
        if l.startswith('encoding video'):
            phase, sub, detail, eta = 'encoding', 0.0, 'starting the encoder', None
        m = RE_FRAME.match(l)
        if m and total_frames:
            f, sp = int(m.group(1)), float(m.group(2))
            phase = 'encoding'
            sub = min(100.0, 100.0 * f / total_frames)
            eta = (total_frames - f) / B.FPS / max(sp, 0.05)
            detail = 'encoding the video'
        if 'DONE ->' in l:
            phase, sub, detail, eta = 'done', 100.0, 'finished', 0
    weights = {'starting': (0, 4), 'mixing': (4, 8), 'rendering': (8, 32),
               'encoding': (32, 100), 'done': (100, 100)}
    lo, hi = weights.get(phase, (0, 4))
    overall = lo + (hi - lo) * (sub / 100.0)
    failed = any(x.startswith('__EXIT__') and not x.endswith(' 0') for x in log)
    if failed:
        detail = 'stopped with an error'
    problems = [x for x in log if x.startswith('PROBLEM') or x.startswith('ERROR')]
    if failed:
        # the builder's own last words are usually the clearest explanation
        tail = [x for x in log if not x.startswith('__EXIT__') and not x.startswith('frame=')]
        if tail and not tail[-1].startswith('Traceback'):
            problems.append(tail[-1])
    return {'phase': phase, 'percent': round(overall, 1), 'detail': detail,
            'eta': round(eta) if eta is not None else None,
            'running': running, 'failed': failed, 'problems': problems[:6]}


def run_build(P, numbered):
    args = [sys.executable, os.path.join(APP, '_build_video.py'), P.path]
    if numbered:
        args.append('--numbered')
    try:
        pr = subprocess.Popen(args, cwd=P.path, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding='utf-8', errors='replace', bufsize=1)
        for line in pr.stdout:
            line = line.rstrip()
            if line:
                # ffmpeg's -stats rewrites one line with \r; keep only the newest part
                line = line.split('\r')[-1] or line
                state['log'].append(line)
                state['log'][:] = state['log'][-400:]
        pr.wait()
        state['log'].append(f'__EXIT__ {pr.returncode}')
    except Exception as ex:
        state['log'].append('ERROR ' + repr(ex))
        state['log'].append('__EXIT__ 1')
    finally:
        state['running'] = False


# ---------------------------------------------------------------- HTTP
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body=b'', ctype='application/json', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, obj, code=200):
        return self._send(code, json.dumps(obj))

    def _file_ranged(self, path, ctype, download_as=None):
        """Serve a file with HTTP Range support - video and audio will not scrub
        without it. Pass download_as to make the browser save it instead."""
        size = os.path.getsize(path)
        rng = self.headers.get('Range')
        start, end, code = 0, size - 1, 200
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)', rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download_as:
            q = urllib.parse.quote(download_as)
            self.send_header('Content-Disposition',
                             f'attachment; filename="{download_as}"; filename*=UTF-8\'\'{q}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(path, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
                left -= len(chunk)

    def _project(self, q):
        return get_project((q.get('p') or [''])[0], self.server.server_address[1])

    def _inside(self, base, name):
        p = os.path.abspath(os.path.join(base, name))
        return p if p.startswith(os.path.abspath(base) + os.sep) and os.path.isfile(p) else None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = urllib.parse.unquote(u.path)

        if path == '/':
            return self._send(200, PAGE, 'text/html; charset=utf-8')

        if path == '/api/buildlog':
            return self._json({'log': state['log'], 'running': state['running'],
                               'project': state['project'],
                               'progress': build_progress(state['log'], state['running'])})

        if path == '/api/projects':
            d = get_project(None, self.server.server_address[1])
            return self._json({'projects': [project_summary(p) for p in PROJECTS],
                               'default': d.id if d else None})

        P = self._project(q)
        if P is None:
            return self._send(404, b'no projects', 'text/plain')

        if path == '/api/state':
            bp = get_project(state['project']) if state['project'] else None
            return self._json(dict({
                'project': {'id': P.id, 'name': P.name, 'folder': os.path.basename(P.path)},
                'entries': read_order(P), 'built': built_info(P), 'videos': videos(P),
                'running': state['running'], 'building': bp.id if (bp and state['running']) else None,
                'building_name': bp.name if bp else None,
                'settings': SETTINGS.load(P.path), 'defaults': SETTINGS.DEFAULTS,
            }, **songs_state(P)))

        if path == '/api/history':
            return self._json({'versions': history_list(P)})

        if path.startswith('/thumb/'):
            try:
                p = make_thumb(P, path[len('/thumb/'):])
            except Exception:
                p = None
            if not p or not os.path.exists(p):
                return self._send(404, b'no', 'text/plain')
            return self._file_ranged(p, 'image/jpeg')

        if path.startswith('/full/'):
            p = self._inside(P.src, path[len('/full/'):])
            if not p:
                return self._send(404, b'no', 'text/plain')
            return self._file_ranged(p, 'image/jpeg')

        if path.startswith('/song/'):
            name = path[len('/song/'):]
            if name not in B.audio_files(P.path):
                return self._send(404, b'no', 'text/plain')
            ext = os.path.splitext(name)[1].lower()
            return self._file_ranged(os.path.join(P.path, name),
                                     AUDIO_TYPES.get(ext, 'application/octet-stream'))

        if path.startswith('/video/') or path.startswith('/download/'):
            grab = path.startswith('/download/')
            name = path[len('/download/'):] if grab else path[len('/video/'):]
            p = self._inside(P.path, name)
            if not p or not p.lower().endswith('.mp4'):
                return self._send(404, b'no', 'text/plain')
            return self._file_ranged(p, 'video/mp4', download_as=os.path.basename(p) if grab else None)

        if path == '/preview':
            kind = (q.get('kind') or ['photo'])[0]
            try:
                rot = int((q.get('rot') or [''])[0])
                rot = rot if rot in (0, 90, 180, 270) else None
            except ValueError:
                rot = None
            try:
                jpg = preview_jpeg(P, kind, (q.get('file') or [''])[0], rot)
            except Exception as ex:
                return self._send(500, repr(ex), 'text/plain')
            if not jpg:
                return self._send(404, b'no', 'text/plain')
            return self._send(200, jpg, 'image/jpeg')

        return self._send(404, b'not found', 'text/plain')

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        buf = bytearray()
        while len(buf) < n:
            chunk = self.rfile.read(min(1 << 20, n - len(buf)))
            if not chunk:
                break
            buf += chunk
        return bytes(buf)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if u.path == '/api/project-new':
            try:
                data = json.loads(self._read_body() or b'{}')
                p = new_project(data.get('name'))
            except Exception as ex:
                return self._json({'error': f'could not create it: {ex}'}, 400)
            return self._json({'ok': True, 'id': p.id})

        P = self._project(q)
        if P is None:
            return self._json({'error': 'no projects'}, 404)

        # The file arrives as the raw body with its name in a header. Multipart
        # would need the cgi module, which Python 3.13+ removed.
        if u.path in ('/api/upload', '/api/song-upload'):
            fname = urllib.parse.unquote(self.headers.get('X-Filename', '') or '')
            if not fname:
                return self._json({'error': 'no file name given'}, 400)
            raw = self._read_body()
            if not raw:
                return self._json({'error': 'empty file'}, 400)
            if u.path == '/api/song-upload':
                try:
                    name = store_song(P, raw, fname)
                except ValueError as ex:
                    return self._json({'error': str(ex)}, 400)
                write_songs(P, B.song_names(P.path) + [name])
                return self._json(dict({'ok': True, 'name': name}, **songs_state(P)))
            try:
                stored, date = store_upload(P, raw, fname)
            except ValueError as ex:
                return self._json({'error': str(ex)}, 400)
            except Exception:
                return self._json({'error': f'could not read "{fname}" as a photo'}, 400)
            snapshot_order(P, 'before-upload')
            ents = read_order(P)
            pos, how = insert_new(ents, {'file': stored, 'date': date or '?',
                                         'removed': False, 'fixed': None})
            write_order(P, ents)
            try:
                make_thumb(P, stored)
            except Exception:
                pass
            return self._json({'ok': True, 'file': stored, 'date': date,
                               'position': pos, 'how': how})

        try:
            data = json.loads(self._read_body() or b'{}')
        except Exception:
            data = {}

        if u.path == '/api/save':
            clean = []
            for e in data.get('entries') or []:
                f = e.get('file', '')
                if not f or not os.path.exists(os.path.join(P.src, f)):
                    continue
                try:
                    fx = float(e['fixed']) if e.get('fixed') else None
                except (TypeError, ValueError):
                    fx = None
                try:
                    rot = int(e.get('rot') or 0) % 360
                except (TypeError, ValueError):
                    rot = 0
                clean.append({'file': f, 'date': e.get('date') or '?',
                              'removed': bool(e.get('removed')), 'fixed': fx,
                              'rot': rot if rot in (0, 90, 180, 270) else 0})
            if not clean and data.get('entries'):
                return self._json({'error': 'nothing to save'}, 400)
            kept = write_order(P, clean)
            return self._json({'ok': True, 'kept': kept, 'total': len(clean)})

        if u.path == '/api/settings':
            old = SETTINGS.load(P.path)
            new = SETTINGS.save(P.path, data.get('settings') or {})
            renamed, note = [], ''
            if new['name'] != old['name']:
                renamed, note = rename_videos(P, old, new)
            return self._json({'ok': True, 'settings': new, 'renamed': renamed, 'note': note})

        if u.path == '/api/songs':
            write_songs(P, data.get('songs') or [])
            return self._json(dict({'ok': True}, **songs_state(P)))

        if u.path == '/api/project-rename':
            name = re.sub(r'\s+', ' ', str(data.get('name') or '')).strip()[:60]
            if not name:
                return self._json({'error': 'type a name'}, 400)
            P.name = name
            save_projects()
            return self._json({'ok': True})

        if u.path == '/api/restore':
            name = os.path.basename(data.get('name') or '')
            src = os.path.join(P.history, name)
            if not name or not os.path.exists(src):
                return self._json({'error': 'version not found'}, 404)
            snapshot_order(P, 'before-restore')
            shutil.copy2(src, P.order)
            return self._json({'ok': True, 'restored': name,
                               'slides': sum(1 for e in read_order(P) if not e['removed'])})

        if u.path == '/api/build':
            with REG_LOCK:
                if state['running']:
                    bp = get_project(state['project'])
                    return self._json({'error': f'already building "{bp.name if bp else "?"}"'}, 409)
                state.update(running=True, project=P.id, log=[])
            threading.Thread(target=run_build, args=(P, bool(data.get('numbered'))),
                             daemon=True).start()
            return self._json({'ok': True})

        return self._json({'error': 'unknown'}, 404)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # MUST stay False on Windows: SO_REUSEADDR there lets a second instance bind a
    # port that is already in use, and the two then fight over connections.
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        """Browsers abort keep-alive and video range requests constantly - normal,
        so don't dump a traceback and frighten whoever is looking at the console."""
        ex = sys.exc_info()[1]
        if isinstance(ex, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def lan_ip():
    """Best-effort LAN address of this machine (no traffic actually sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


PAGE = open(os.path.join(APP, '_page.html'), encoding='utf-8').read()


def main():
    if not PROJECTS:
        # first run: start with one empty project so there is something to open
        new_project('My slideshow').port = DEFAULT_PORT
        save_projects()
    print('Photo Slideshow Editor')
    for p in PROJECTS:
        print(f'  project: {p.name}  ({p.path})')
    threading.Thread(target=prebuild_thumbs, daemon=True).start()

    # Every project that has its own address keeps it (old bookmarks keep working
    # and open that project); they all serve the same editor.
    ports = sorted({p.port for p in PROJECTS if p.port} | {DEFAULT_PORT})
    servers = []
    for port in ports:
        try:
            servers.append(Server(('0.0.0.0', port), Handler))     # 0.0.0.0 = reachable on the LAN
        except OSError:
            print(f'  port {port} is busy (is another copy of the editor running?) - skipped')
    if not servers:
        sys.exit('could not open any port')

    ip = lan_ip()
    print()
    for s in servers:
        port = s.server_address[1]
        p = get_project(None, port)
        print(f'  {p.name}:')
        print(f'     this computer:     http://127.0.0.1:{port}/')
        if ip and not ip.startswith('127.'):
            print(f'     phone/Mac on wi-fi: http://{ip}:{port}/')
    print()
    print('  Every address can switch to any project. Anyone on your wi-fi who opens')
    print('  one can change the slideshows, so only use it at home.')
    print('  (close this window when you are finished)')
    print(flush=True)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    if '--no-browser' not in sys.argv:
        # "--project <id>" (used by each project folder's launcher) opens that project
        url = f'http://127.0.0.1:{servers[0].server_address[1]}/'
        if '--project' in sys.argv[:-1]:
            url += '#p=' + urllib.parse.quote(sys.argv[sys.argv.index('--project') + 1])
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        print('\n  closed.')


if __name__ == '__main__':
    main()
