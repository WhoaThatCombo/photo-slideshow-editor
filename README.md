# Photo Slideshow Editor

A small local web app for putting together photo slideshow videos — arrange the
photos in a browser, pick the music and the look, press **Build video**, get an MP4.

It runs on one computer and can be used from any phone, tablet or laptop on the
same wi-fi, so the person arranging the photos doesn't need to be at the PC.

## Features

- **Photo grid** — drag to reorder, turn sideways photos, remove/restore, keep a
  photo on screen longer, full-size viewer, search and filters, undo/redo,
  keyboard shortcuts.
- **Adding photos** — pick or drag-and-drop; iPhone HEIC is converted
  automatically and photos are dated from their EXIF data.
- **Several projects** — switch between slideshows from the header; each project
  has its own photos, songs, settings and videos.
- **Music** — song list with preview, reordering and uploads. Either fit the
  photos to the length of the music, or set the seconds per photo and loop the
  music / finish in silence. Blend between songs and fade out at the end.
- **Look** — 11 transitions (or a mix), whole-photo or fill-the-screen, blurred /
  black / white / cream background, date captions, an opening title and an
  ending message in three lettering styles, 720p / 1080p / 4K, a seamless-loop
  option for TVs and photo frames, and a numbered review copy.
- **Preview** — see any photo or the title card exactly as it will appear in the
  video before building.
- **Safe to experiment** — every save keeps a timestamped copy of the photo
  order, restorable from *Earlier versions*.

## Requirements

- Python 3.9 or newer
- [Pillow](https://pypi.org/project/pillow/) (`pip install pillow`)
- Optional: [pillow-heif](https://pypi.org/project/pillow-heif/) for iPhone HEIC photos
- [FFmpeg](https://ffmpeg.org/) (with ffprobe) on the PATH —
  on Windows: `winget install Gyan.FFmpeg`

Built and used on Windows. The title fonts come from `C:\Windows\Fonts`; on other
systems it falls back to a default font.

## Quick start

```
pip install pillow pillow-heif
python _photo_editor.py
```

or double-click `Start Photo Editor.bat` on Windows. A browser opens on
`http://127.0.0.1:8778/` with an empty project called *My slideshow*. The console
also prints the address to open from other devices on your wi-fi.

## Projects

Projects are listed in `projects.json` (created on first run; see
`projects.example.json`). Each project is a folder:

```
My slideshow/
  project/photo_order.txt   the running order (plain text, editable by hand)
  project/*.jpg             the photos
  _song_order.txt           the songs, in play order
  *.mp3 / *.m4a / ...       the songs
  video_settings.json       timing, transitions, title, music options...
  <name>.mp4                the built video
```

A project can have its own `port`; that address then opens on that project
(handy for bookmarks). Every address can switch to every project.
*New project…* creates folders under `projects_dir` (default: `projects/` here).

## Security

There is no login. Anyone on your network who opens the address can view and
change the slideshows, so only run it on a home network you trust.

## License

MIT
