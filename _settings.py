"""Video settings for one slideshow project, shared by the editor and the builder.

Stored as video_settings.json inside each project folder. Everything is validated
here, so a hand-edited or half-written file can never feed the builder a bad value.
"""
import os, re, json

FILE = 'video_settings.json'

DEFAULTS = {
    'name': 'Slideshow',           # output file name, without .mp4
    'seconds': 5.0,                # how long each photo stays on screen (timing = fixed)
    'transition': 'fade',
    'xf': 0.7,                     # transition length
    'fit': 'fit',                  # fit = whole photo, fill = crop to the screen
    'background': 'blur',          # what shows around a photo that doesn't fill the screen
    'captions': 'off',             # date under each photo
    'title': '',                   # optional opening card
    'subtitle': '',
    'ending': '',                  # optional closing card
    'font': 'elegant',
    'title_seconds': 6.0,
    'resolution': '1080',
    'quality': 'good',
    'fade_ends': True,             # fade from/to black at the very start and end
    'loop': False,                 # end blends back into the start (TV / photo frame)
    # music
    'music': False,                # play the project's songs
    'timing': 'fixed',             # fixed = 'seconds' per photo, fit = photos share the music's length
    'song_xf': 0.0,                # blend between one song and the next
    'music_fade': 5.0,             # fade the music out over the last N seconds
    'music_end': 'loop',           # photos outlast the music: loop it, or finish in silence
}

CHOICES = {
    'transition': ['fade', 'dissolve', 'fadeblack', 'fadewhite', 'smoothleft', 'slideleft',
                   'wipeleft', 'circleopen', 'radial', 'mixed', 'none'],
    'fit': ['fit', 'fill'],
    'background': ['blur', 'black', 'white', 'cream'],
    'captions': ['off', 'full', 'month', 'year'],
    'font': ['elegant', 'modern', 'handwritten'],
    'resolution': ['720', '1080', '2160'],
    'quality': ['best', 'good', 'small'],
    'timing': ['fixed', 'fit'],
    'music_end': ['loop', 'silence'],
}

RANGES = {'seconds': (2.0, 30.0), 'xf': (0.3, 2.0), 'title_seconds': (3.0, 20.0),
          'song_xf': (0.0, 5.0), 'music_fade': (0.0, 10.0)}
TEXT = {'title', 'subtitle', 'ending'}

BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CTRL = re.compile(r'[\x00-\x1f\x7f]')


def clean_name(s, default=None):
    s = BAD_NAME.sub('', str(s or '')).strip()
    if s.lower().endswith('.mp4'):
        s = s[:-4]
    s = s.strip().strip('.').strip()
    return s[:80] or default or DEFAULTS['name']


def sanitize(d):
    out = dict(DEFAULTS)
    for k, v in (d or {}).items():
        if k not in DEFAULTS:
            continue
        if k == 'name':
            out[k] = clean_name(v)
        elif isinstance(DEFAULTS[k], bool):
            out[k] = bool(v)
        elif k in CHOICES:
            if str(v) in CHOICES[k]:
                out[k] = str(v)
        elif k in RANGES:
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            lo, hi = RANGES[k]
            out[k] = round(min(hi, max(lo, x)), 2)
        elif k in TEXT:
            out[k] = CTRL.sub(' ', str(v or '')).strip()[:120]
    # a transition has to be shorter than the slides it joins
    out['xf'] = round(min(out['xf'], out['seconds'] * 0.6), 2)
    return out


def load(pdir):
    try:
        with open(os.path.join(pdir, FILE), encoding='utf-8') as f:
            return sanitize(json.load(f))
    except Exception:
        return dict(DEFAULTS)


def save(pdir, d):
    s = sanitize(d)
    path = os.path.join(pdir, FILE)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=1, ensure_ascii=False)
    os.replace(path + '.tmp', path)
    return s


def outname(s, numbered=False):
    return s['name'] + (' (NUMBERED review)' if numbered else '') + '.mp4'
