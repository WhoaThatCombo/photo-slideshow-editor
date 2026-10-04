"""Builds one project's slideshow video.

  python _build_video.py <project folder>             -> <name>.mp4 in that folder
  python _build_video.py <project folder> --numbered  -> <name> (NUMBERED review).mp4

The photos and their order come from <project>/project/photo_order.txt, the songs
from <project>/_song_order.txt, and everything else (timing, transitions, title,
music...) from <project>/video_settings.json - all written by the Photo Editor.

Normally you don't run this by hand - the Photo Editor's Build button does it.
The editor also imports this file to draw its previews, so nothing heavy happens
at import time.
"""
import os, re, sys, json, glob, shutil, subprocess
from PIL import Image, ImageOps, ImageFilter, ImageEnhance, ImageDraw, ImageFont

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import _settings

FONTDIR = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
FPS = 30
MARGIN = 0.94
FADE_IN = 1.0
RES = {'720': (1280, 720), '1080': (1920, 1080), '2160': (3840, 2160)}
CRF = {'best': 17, 'good': 20, 'small': 25}
PRESET = {'best': 'slow', 'good': 'medium', 'small': 'medium'}
MIXED = ['fade', 'smoothleft', 'dissolve', 'circleopen', 'smoothright', 'fadeblack']
BG = {'black': (0, 0, 0), 'white': (250, 250, 248), 'cream': (244, 236, 222)}
# (title font, subtitle font) for each lettering style
FONTS = {'elegant': ('georgia.ttf', 'georgiai.ttf'),
         'modern': ('seguisb.ttf', 'segoeuil.ttf'),
         'handwritten': ('segoepr.ttf', 'segoepr.ttf')}
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
          'August', 'September', 'October', 'November', 'December']
AUDIO_EXT = ('.mp3', '.m4a', '.opus', '.wav', '.flac', '.aac', '.ogg', '.wma')
SONGS_FILE = '_song_order.txt'

# <number> | <date> | <filename>  [| <seconds>] [| rot90]
LINE = re.compile(r'^\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*(\S*)\s*\|\s*'
                  r'([^|]+?)\s*(?:\|\s*([^|]*?)\s*)?(?:\|\s*([^|]*?)\s*)?$')
ROT = re.compile(r'^rot(90|180|270)$', re.I)
NUM = re.compile(r'^([0-9]+(?:\.[0-9]+)?)\s*s?$', re.I)


def extras(m):
    """(hold_seconds, rotation_degrees) from the optional trailing columns."""
    fixed, rot = None, 0
    for x in (m.group(4), m.group(5)):
        x = (x or '').strip()
        if not x:
            continue
        r = ROT.match(x)
        n = NUM.match(x)
        if r:
            rot = int(r.group(1))
        elif n:
            fixed = float(n.group(1))
    return fixed, rot


def read_order(pdir):
    src = os.path.join(pdir, 'project')
    entries, bad = [], []
    for ln, raw in enumerate(open(os.path.join(src, 'photo_order.txt'), encoding='utf-8'), 1):
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        m = LINE.match(s)
        if not m:
            bad.append(f'line {ln}: cannot read "{s[:60]}"')
            continue
        num, date, fname = float(m.group(1)), m.group(2), m.group(3)
        fixed, rot = extras(m)
        if not os.path.exists(os.path.join(src, fname)):
            bad.append(f'line {ln}: file not found - {fname}')
            continue
        entries.append({'num': num, 'date': date, 'file': fname, 'line': ln,
                        'fixed': fixed, 'rot': rot})
    entries.sort(key=lambda e: (e['num'], e['line']))
    return entries, bad


def audio_files(pdir):
    return sorted(f for f in os.listdir(pdir)
                  if f.lower().endswith(AUDIO_EXT) and os.path.isfile(os.path.join(pdir, f)))


def song_names(pdir):
    """Song running order from _song_order.txt (only songs that exist, each once).
    Without that file, every audio file in the folder, in name order."""
    path = os.path.join(pdir, SONGS_FILE)
    if not os.path.exists(path):
        return audio_files(pdir)
    out = []
    for raw in open(path, encoding='utf-8'):
        s = raw.strip()
        if s and not s.startswith('#') and s not in out and \
                os.path.isfile(os.path.join(pdir, s)):
            out.append(s)
    return out


# ---------------------------------------------------------------- drawing
class Look:
    """The settings plus the numbers derived from them."""
    def __init__(self, s):
        self.s = s
        self.W, self.H = RES[s['resolution']]
        self.k = self.H / 1080                      # scale for text and spacing
        # "no transition" is a one-frame blend, so the timing maths stays the same
        self.xf = 1 / FPS if s['transition'] == 'none' else s['xf']

    def transition(self, k):
        t = self.s['transition']
        if t == 'mixed':
            return MIXED[(k - 1) % len(MIXED)]
        return 'fade' if t == 'none' else t


def font(name, size):
    for n in (name, 'arialbd.ttf'):
        try:
            return ImageFont.truetype(os.path.join(FONTDIR, n), size)
        except OSError:
            pass
    return ImageFont.load_default()


def open_photo(src, name, rot=0):
    with Image.open(os.path.join(src, name)) as im:
        im = ImageOps.exif_transpose(im)
        if rot:
            # the editor's rotate button turns clockwise; PIL's rotate is
            # anticlockwise, hence the minus
            im = im.rotate(-rot, expand=True)
        if im.mode != 'RGB':
            im = im.convert('RGB')
        im.load()
        return im


def backdrop(im, L, dim=0.45):
    """What fills the screen behind a photo."""
    W, H = L.W, L.H
    if L.s['background'] == 'blur' and im is not None:
        # blur a quarter-size copy: same look, a fraction of the work (matters at 4K)
        small = ImageOps.fit(im, (max(1, W // 4), max(1, H // 4)), Image.BILINEAR)
        small = small.filter(ImageFilter.GaussianBlur(11))
        return ImageEnhance.Brightness(small.resize((W, H), Image.BICUBIC)).enhance(dim)
    return Image.new('RGB', (W, H), BG.get(L.s['background'], (0, 0, 0)))


def caption_text(date, mode):
    if mode == 'off':
        return None
    m = re.search(r'(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?', date or '')
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if mode == 'year' or not mo or not 1 <= int(mo) <= 12:
        return y
    if mode == 'month' or not d:
        return f'{MONTHS[int(mo) - 1]} {y}'
    return f'{int(d)} {MONTHS[int(mo) - 1]} {y}'


def draw_caption(img, text, L):
    k = L.k
    f = font('seguisb.ttf', int(36 * k))
    d = ImageDraw.Draw(img, 'RGBA')
    tb = d.textbbox((0, 0), text, font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    px, py = int(24 * k), int(13 * k)
    x, y = (L.W - tw) // 2, L.H - int(56 * k) - th
    d.rounded_rectangle([x - px, y - py, x + tw + px, y + th + py],
                        radius=(th + 2 * py) // 2, fill=(0, 0, 0, 150))
    d.text((x - tb[0], y - tb[1]), text, font=f, fill=(255, 255, 255, 245))


def draw_number(img, n, date, L):
    k = L.k
    big, small = font('arialbd.ttf', int(108 * k)), font('arialbd.ttf', int(34 * k))
    d = ImageDraw.Draw(img, 'RGBA')
    lab = str(n)
    tb = d.textbbox((0, 0), lab, font=big)
    bw, bh = tb[2] - tb[0], tb[3] - tb[1]
    pad, x0, y0 = int(26 * k), int(28 * k), int(24 * k)
    d.rectangle([x0, y0, x0 + bw + pad * 2, y0 + bh + pad * 2 + int(44 * k)], fill=(0, 0, 0, 165))
    d.text((x0 + pad - tb[0], y0 + pad - tb[1]), lab, font=big, fill=(255, 236, 120))
    d.text((x0 + pad, y0 + pad + bh + int(8 * k)), date or 'no date', font=small,
           fill=(235, 235, 235))


def compose_photo(im, L, date=None, number=None):
    W, H = L.W, L.H
    if L.s['fit'] == 'fill':
        # centre slightly above the middle - that is usually where the faces are
        canvas = ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(0.5, 0.42))
    else:
        canvas = backdrop(im, L)
        margin = 1.0 if abs((im.width / im.height) / (W / H) - 1) < 0.02 else MARGIN
        sc = min(int(W * margin) / im.width, int(H * margin) / im.height)
        fw, fh = max(1, round(im.width * sc)), max(1, round(im.height * sc))
        fg = im.resize((fw, fh), Image.LANCZOS)
        if sc > 1.05:
            fg = fg.filter(ImageFilter.UnsharpMask(1.6, int(min(90, 40 * sc)), 3))
        canvas.paste(fg, ((W - fw) // 2, (H - fh) // 2))
    cap = caption_text(date, L.s['captions'])
    if cap:
        draw_caption(canvas, cap, L)
    if number is not None:
        draw_number(canvas, number, date, L)
    return canvas


def wrap(d, text, f, maxw):
    lines, cur = [], ''
    for w in (text or '').split():
        t = (cur + ' ' + w).strip()
        if cur and d.textlength(t, font=f) > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def text_card(lines, L, bg_im=None):
    """An opening title or closing message."""
    s, W, H, k = L.s, L.W, L.H, L.k
    if s['background'] in ('white', 'cream'):
        canvas = Image.new('RGB', (W, H), BG[s['background']])
        fg, sub, acc, shadow = (58, 46, 40), (115, 96, 86), (196, 140, 110), None
    else:
        if s['background'] == 'blur' and bg_im is not None:
            canvas = backdrop(bg_im, L, dim=0.30)
        else:
            canvas = Image.new('RGB', (W, H), (12, 12, 14))
        fg, sub, acc, shadow = (255, 255, 255), (228, 220, 212), (240, 190, 160), (0, 0, 0, 110)
    tf, sf = FONTS.get(s['font'], FONTS['elegant'])
    ft, fs = font(tf, int(104 * k)), font(sf, int(50 * k))
    d = ImageDraw.Draw(canvas, 'RGBA')
    title, subtitle = (list(lines) + ['', ''])[:2]
    tl, sl = wrap(d, title, ft, int(W * 0.82)), wrap(d, subtitle, fs, int(W * 0.80))
    lt, ls = int(ft.size * 1.28), int(fs.size * 1.35)
    gap = int(36 * k)
    rule = bool(tl and sl)
    total = len(tl) * lt + len(sl) * ls + (2 * gap if rule else 0)
    y = (H - total) // 2
    off = max(2, int(3 * k))

    def put(line, f, col, y):
        if shadow:
            d.text((W / 2 + off, y + off), line, font=f, fill=shadow, anchor='ma')
        d.text((W / 2, y), line, font=f, fill=col, anchor='ma')

    for line in tl:
        put(line, ft, fg, y)
        y += lt
    if rule:
        y += gap // 2
        d.line([(W / 2 - 70 * k, y), (W / 2 + 70 * k, y)], fill=acc, width=max(2, int(3 * k)))
        y += gap + gap // 2
    for line in sl:
        put(line, fs, sub, y)
        y += ls
    return canvas


# ---------------------------------------------------------------- the video
def plan(entries, L):
    """Every slide in play order. 'flex' slides get their length later - a fixed
    number of seconds, or a share of the music."""
    s = L.s
    slides = []
    if s['title'] or s['subtitle']:
        slides.append({'kind': 'text', 'lines': [s['title'], s['subtitle']],
                       'dur': s['title_seconds']})
    for n, e in enumerate(entries, 1):
        slides.append(dict(e, kind='photo', n=n, dur=e['fixed'], flex=not e['fixed']))
    if s['ending']:
        slides.append({'kind': 'text', 'lines': [s['ending'], ''], 'dur': s['title_seconds']})
    if s['loop'] and len(slides) > 1:
        # finish on a copy of the first slide, so the last frame IS the first frame
        # and a player set to repeat shows no jump
        slides.append({'kind': 'copy', 'dur': L.xf + 0.1})
    return slides


def render(slides, src, work, numbered, L):
    os.makedirs(work, exist_ok=True)
    for f in os.listdir(work):
        os.remove(os.path.join(work, f))
    first = None
    photos = [x for x in slides if x['kind'] == 'photo']
    for i, sl in enumerate(slides, 1):
        out = os.path.join(work, f'{i:04d}.jpg')
        if sl['kind'] == 'copy':
            shutil.copyfile(os.path.join(work, '0001.jpg'), out)
            continue
        if sl['kind'] == 'text':
            if first is None and photos:
                first = open_photo(src, photos[0]['file'], photos[0]['rot'])
            img = text_card(sl['lines'], L, first)
        else:
            im = open_photo(src, sl['file'], sl.get('rot', 0))
            img = compose_photo(im, L, sl['date'], sl['n'] if numbered else None)
        img.save(out, 'JPEG', quality=92, subsampling=1)
        if i % 20 == 0 or i == len(slides):
            print(f'rendered {i}/{len(slides)}', flush=True)


def find_ffmpeg():
    cands = glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft',
                                   'WinGet', 'Packages', 'Gyan.FFmpeg*', '**', 'ffmpeg.exe'),
                      recursive=True)
    if cands:
        return cands[0]
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    sys.exit('ffmpeg not found. Install with:  winget install Gyan.FFmpeg')


def probe_seconds(ffprobe, path):
    out = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', path], capture_output=True, text=True).stdout.strip()
    return float(out)


def mix_songs(ffmpeg, songs, blend, out, stampf):
    """Join the songs into one track (cached until the songs or the blend change)."""
    stamp = '|'.join(f'{os.path.basename(s)}:{os.path.getsize(s)}:{int(os.path.getmtime(s))}'
                     for s in songs) + f'|xf{blend}'
    have = open(stampf, encoding='utf-8').read() if os.path.exists(stampf) else ''
    if os.path.exists(out) and have == stamp:
        print('soundtrack unchanged, reusing', flush=True)
        return
    print('mixing soundtrack...', flush=True)
    # decode-and-join in the filter graph, not the concat demuxer: the songs can be
    # different formats and sample rates (mp3 + opus), which the demuxer can't join
    cmd = [ffmpeg, '-y', '-v', 'error']
    for s in songs:
        cmd += ['-i', s]
    n = len(songs)
    g = ''.join(f'[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}];'
                for i in range(n))
    if n == 1:
        g += '[a0]anull[out]'
    elif blend > 0:
        prev = 'a0'
        for i in range(1, n):
            nxt = 'out' if i == n - 1 else f'c{i}'
            g += f'[{prev}][a{i}]acrossfade=d={blend}:c1=tri:c2=tri[{nxt}];'
            prev = nxt
        g = g.rstrip(';')
    else:
        g += ''.join(f'[a{i}]' for i in range(n)) + f'concat=n={n}:v=0:a=1[out]'
    cmd += ['-filter_complex', g, '-map', '[out]', '-c:a', 'aac', '-b:a', '192k', out]
    subprocess.run(cmd, check=True)
    open(stampf, 'w', encoding='utf-8').write(stamp)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit('usage: _build_video.py <project folder> [--numbered]')
    pdir = os.path.abspath(args[0])
    numbered = '--numbered' in sys.argv
    src = os.path.join(pdir, 'project')
    workroot = os.path.join(pdir, '_workfiles')
    work = os.path.join(workroot, 'numbered' if numbered else 'plain')

    S = _settings.load(pdir)
    L = Look(S)
    ffmpeg = find_ffmpeg()
    ffprobe = os.path.join(os.path.dirname(ffmpeg), 'ffprobe.exe')
    if not os.path.exists(ffprobe):
        ffprobe = shutil.which('ffprobe') or 'ffprobe'
    outname = _settings.outname(S, numbered)
    XF = L.xf

    entries, bad = read_order(pdir)
    for b in bad:
        print('PROBLEM:', b, flush=True)
    if not entries:
        sys.exit('there are no photos in this video yet')

    # ---- music
    songs = [os.path.join(pdir, n) for n in song_names(pdir)] if S['music'] else []
    if S['music'] and not songs:
        print('PROBLEM: music is switched on but there are no songs - building without music',
              flush=True)
    music_len = 0.0
    if songs:
        lens = [probe_seconds(ffprobe, s) for s in songs]
        for s, d in zip(songs, lens):
            print(f'   song: {os.path.basename(s)}  ({d/60:.1f} min)', flush=True)
        blend = min(S['song_xf'], min(lens) / 2) if len(songs) > 1 else 0
        music_len = sum(lens) - (len(songs) - 1) * blend
        print(f'   music: {music_len/60:.1f} min total', flush=True)

    # ---- timing
    slides = plan(entries, L)
    N = len(slides)
    flex = [x for x in slides if x.get('flex')]
    fixed_total = sum(x['dur'] for x in slides if not x.get('flex'))
    if songs and S['timing'] == 'fit' and flex:
        D = (music_len + (N - 1) * XF - fixed_total) / len(flex)
        if D <= XF + 0.3:
            sys.exit(f'{len(entries)} photos is too many for {music_len/60:.1f} minutes of music - '
                     'add another song, remove some photos, or set the seconds per photo yourself')
    else:
        D = S['seconds']
    for x in flex:
        x['dur'] = D
    durs = [x['dur'] for x in slides]
    if any(d <= XF for d in durs):
        sys.exit(f'a slide is shorter than the {XF}s transition - lengthen it or shorten the transition')
    tot = sum(durs) - (N - 1) * XF
    fade_out = 3.0 if songs else 1.5

    print(f'{len(entries)} photos, {N} slides, {S["resolution"]}p, {S["transition"]} transitions, '
          + ('with music' if songs else 'no audio'), flush=True)
    os.makedirs(workroot, exist_ok=True)
    audio = os.path.join(workroot, 'soundtrack.m4a')
    if songs:
        mix_songs(ffmpeg, songs, blend, audio, os.path.join(workroot, 'soundtrack.stamp'))

    print('rendering slides...', flush=True)
    render(slides, src, work, numbered, L)

    # ---- filter graph
    lines = [f'[{i}:v]fps={FPS},format=yuv420p,setsar=1,settb=AVTB[s{i}];' for i in range(N)]
    prev, starts, acc = 's0', [0.0], 0.0
    for k in range(1, N):
        # offset of the k-th transition = everything so far, minus the overlaps
        acc += durs[k - 1]
        off = acc - k * XF
        starts.append(off)
        lines.append(f'[{prev}][s{k}]xfade=transition={L.transition(k)}:duration={XF:.4f}:'
                     f'offset={off:.4f}[x{k}];')
        prev = f'x{k}'
    if S['fade_ends'] and not S['loop']:
        lines.append(f'[{prev}]fade=t=in:st=0:d={FADE_IN},'
                     f'fade=t=out:st={tot - fade_out:.3f}:d={fade_out}[v]' + (';' if songs else ''))
    else:
        lines.append(f'[{prev}]null[v]' + (';' if songs else ''))
    if songs:
        mf = min(S['music_fade'], tot / 2)
        a = f'[{N}:a]apad,atrim=0:{tot:.3f},asetpts=PTS-STARTPTS'
        if mf > 0:
            a += f',afade=t=out:st={tot - mf:.3f}:d={mf:.3f}'
        lines.append(a + '[a]')
    graph = os.path.join(workroot, 'graph.txt')
    open(graph, 'w', encoding='utf-8').write('\n'.join(lines))

    cmd = [ffmpeg, '-y', '-v', 'error', '-stats']
    for i in range(1, N + 1):
        cmd += ['-loop', '1', '-framerate', str(FPS), '-t', f'{durs[i-1]:.4f}',
                '-i', f'{i:04d}.jpg']
    if songs:
        # play the music again if the photos outlast it (when that option is on)
        if S['music_end'] == 'loop' and tot > music_len + 0.05:
            cmd += ['-stream_loop', '-1']
        cmd += ['-i', audio]
    cmd += ['-filter_complex_script', graph, '-map', '[v]']
    cmd += ['-map', '[a]', '-c:a', 'aac', '-b:a', '192k'] if songs else ['-an']
    cmd += ['-c:v', 'libx264', '-preset', PRESET[S['quality']], '-crf', str(CRF[S['quality']]),
            '-pix_fmt', 'yuv420p', '-r', str(FPS), '-t', f'{tot:.3f}', '-movflags', '+faststart',
            os.path.join(pdir, outname)]
    print(f'{N} slides x {D:.3f}s -> {tot:.1f}s', flush=True)
    print('encoding video (this takes a couple of minutes)...', flush=True)
    rc = subprocess.run(cmd, cwd=work).returncode
    if rc == 0:
        ph = [(x, s, d) for x, s, d in zip(slides, starts, durs) if x['kind'] == 'photo']
        info = {
            'video': outname, 'slide_seconds': D, 'crossfade': XF,
            'duration': tot, 'numbered': numbered, 'music': bool(songs),
            'slides': [x['file'] for x, _, _ in ph],
            'times': [{'file': x['file'], 'start': round(s, 3), 'dur': round(d, 3)}
                      for x, s, d in ph],
        }
        json.dump(info, open(os.path.join(pdir, '_built_order.json'), 'w'), indent=1)
        print('DONE ->', outname, flush=True)
    else:
        print('ffmpeg failed with code', rc, flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
