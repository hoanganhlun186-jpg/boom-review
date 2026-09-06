"""Shared geometry and final compositing for the interactive preview.

Logo/subtitle positions are centers in the final frame. Blur boxes are
normalized within the source picture, excluding title bands.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def title_padding(width, header, footer):
    """Even pixel bands, independently enabled by nonempty title text."""
    pad = max(100, round(200*max(.5, width/1920)))
    pad += pad % 2
    return (pad if header else 0, pad if footer else 0)


def normalized_box(box):
    w = clamp(box.get('w', .8), .01, 1)
    h = clamp(box.get('h', .12), .01, 1)
    return dict(x=clamp(box.get('x', .1), 0, 1-w),
                y=clamp(box.get('y', .75), 0, 1-h), w=w, h=h)


def resize_box(box, handle, dx, dy):
    b = normalized_box(box)
    left, top = b['x'], b['y']
    right, bottom = left+b['w'], top+b['h']
    if handle == 'move':
        return dict(b, x=clamp(left+dx, 0, 1-b['w']), y=clamp(top+dy, 0, 1-b['h']))
    if 'l' in handle: left = clamp(left+dx, 0, right-.01)
    if 'r' in handle: right = clamp(right+dx, left+.01, 1)
    if 't' in handle: top = clamp(top+dy, 0, bottom-.01)
    if 'b' in handle: bottom = clamp(bottom+dy, top+.01, 1)
    return dict(x=left, y=top, w=right-left, h=bottom-top)


def read_cues(path):
    if not path or not os.path.isfile(path):
        return []
    text = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    pattern = r'(\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)'
    def seconds(t):
        h, m, s = t.replace(',', '.').split(':')
        return int(h)*3600 + int(m)*60 + float(s)
    return [(seconds(a), seconds(b), re.sub(r'<[^>]+>', '', c).strip())
            for a, b, c in re.findall(pattern, text, re.S)]


def wrap_caption(text, width, size):
    """Use the same explicit line breaks for the preview and ASS output."""
    from PIL import ImageFont
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', size)
    except OSError:
        font = ImageFont.load_default(size=size)
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = (line+' '+word).strip()
            if line and font.getlength(candidate) > width*.9:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return '\n'.join(lines)


FONT_FILES = {'Arial':'arial.ttf','Tahoma':'tahoma.ttf','Verdana':'verdana.ttf',
              'Times New Roman':'times.ttf','Segoe UI':'segoeui.ttf','Impact':'impact.ttf',
              'Consolas':'consola.ttf','Courier New':'cour.ttf'}

def single_line_caption(text, width, size, family='Arial'):
    """Keep a cue on one row; shrink long cues without changing their timing."""
    from PIL import ImageFont
    text = ' '.join(text.split())
    size = max(1,int(size))
    def font_at(value):
        try:
            return ImageFont.truetype('C:/Windows/Fonts/'+FONT_FILES.get(family,'arial.ttf'),value)
        except OSError:
            return ImageFont.load_default(size=value)
    low,high = 1,size
    while low<high:
        mid = (low+high+1)//2
        if font_at(mid).getlength(text)<=width*.9:
            low = mid
        else:
            high = mid-1
    return text,low


def write_ass(path, cues, width, height, design):
    colors = {'white':'FFFFFF', 'yellow':'00FFFF', 'cyan':'FFFF00',
              'green':'008000', 'orange':'00A5FF', 'pink':'CBC0FF'}
    color = colors.get(design.get('sub_color'), 'FFFFFF')
    size = int(clamp(design.get('sub_size', 36), 8, 200))
    outline = int(clamp(design.get('sub_outline', 2), 0, 12))
    family = design.get('sub_font','Arial')
    if family not in FONT_FILES: family = 'Arial'
    bg = str(design.get('sub_background_color','#000000')).lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}',bg): bg = '000000'
    alpha = round(255*(1-clamp(design.get('sub_background_opacity',70),0,100)/100))
    bg_ass = f'&H{alpha:02X}{bg[4:6]}{bg[2:4]}{bg[0:2]}'
    x = round(clamp(design.get('sub_x', .5))*width)
    y = round(clamp(design.get('sub_y', .78))*height)
    def stamp(t):
        cs = max(0, round(t*100))
        return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
    rows = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}',
            f'PlayResY: {height}', 'WrapStyle: 0', 'ScaledBorderAndShadow: yes',
            '[V4+ Styles]',
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
            f'Style: Default,{family},{size},&H00{color},&H00{color},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{outline},0,5,10,10,10,1',
            f'Style: Backdrop,{family},{size},&HFF000000,&HFF000000,{bg_ass},{bg_ass},0,0,0,0,100,100,0,0,3,4,0,5,10,10,10,1',
            '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for start, end, text in cues:
        text,cue_size = single_line_caption(text,width,size,family)
        # SRT is text, never ASS override instructions.
        text = text.replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\n', r'\N')
        if design.get('sub_background',False):
            rows.append(f'Dialogue: 0,{stamp(start)},{stamp(end)},Backdrop,,0,0,0,,{{\\fs{cue_size}\\q2\\pos({x},{y})}}{text}')
        rows.append(f'Dialogue: 1,{stamp(start)},{stamp(end)},Default,,0,0,0,,{{\\fs{cue_size}\\q2\\pos({x},{y})}}{text}')
    Path(path).write_text('\n'.join(rows), encoding='utf-8-sig')


def fingerprint(design):
    result = dict(design or {})
    for key in ('logo', 'srt'):
        p = result.get(key)
        if p and os.path.isfile(p):
            stat = os.stat(p)
            result[key+'_stamp'] = [stat.st_mtime_ns, stat.st_size]
    return result


def apply_design(video_path, design, has_title_bands=False):
    """Composite into a temporary output, replacing the video only on success."""
    design = design or {}
    if not (design.get('logo') or design.get('blur_boxes') or design.get('burn')):
        return
    from utils.helpers import FFmpegUtils
    probe = subprocess.run([FFmpegUtils.ffprobe_executable(), '-v', 'error',
                            '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                            '-of', 'json', str(video_path)],
                           **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True, check=True))
    stream = json.loads(probe.stdout)['streams'][0]
    w, h = int(stream['width']), int(stream['height'])
    if isinstance(has_title_bands, (tuple,list)):
        top_on,bottom_on = has_title_bands
    else:
        top_on = bottom_on = bool(has_title_bands)
    pad,bottom_pad = title_padding(w,top_on,bottom_on)
    content_h = max(1, h-pad-bottom_pad)
    with tempfile.TemporaryDirectory(prefix='preview_design_', dir=str(Path(video_path).parent)) as work:
        filters, label = [], '0:v'
        for i, box in enumerate(design.get('blur_boxes') or []):
            b = normalized_box(box)
            bw, bh = max(1, round(b['w']*w)), max(1, round(b['h']*content_h))
            x, y = min(w-bw, round(b['x']*w)), pad+min(content_h-bh, round(b['y']*content_h))
            filters.append(f'[{label}]split=2[base{i}][crop{i}];[crop{i}]crop={bw}:{bh}:{x}:{y},gblur=sigma=20[blur{i}];[base{i}][blur{i}]overlay={x}:{y}[box{i}]')
            label = f'box{i}'
        if design.get('burn'):
            cues = read_cues(design.get('srt'))
            if not cues:
                raise ValueError('Đã bật khắc sub nhưng file SRT chưa có nội dung hợp lệ.')
            write_ass(Path(work)/'captions.ass', cues, w, h, design)
            filters.append(f'[{label}]ass=filename=captions.ass[subbed]')
            label = 'subbed'
        cmd = [FFmpegUtils.ffmpeg_executable(), '-y', '-hide_banner', '-loglevel', 'error', '-i', str(Path(video_path).resolve())]
        if design.get('logo'):
            cmd += ['-i', str(Path(design['logo']).resolve())]
            lw = max(1, round(w*clamp(design.get('logo_size', 10), 1, 50)/100))
            x, y = clamp(design.get('logo_x', .85)), clamp(design.get('logo_y', .05))
            filters.append(f"[1:v]scale={lw}:-1[logo];[{label}][logo]overlay=x='max(0,min(W-w,W*{x}-w/2))':y='max(0,min(H-h,H*{y}-h/2))':eof_action=repeat[branded]")
            label = 'branded'
        output = Path(work)/'designed.mp4'
        cmd += ['-filter_complex', ';'.join(filters), '-map', f'[{label}]', '-map', '0:a?',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-c:a', 'copy', '-movflags', '+faststart', str(output.resolve())]
        run = subprocess.run(cmd, cwd=work, **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True))
        if run.returncode:
            raise RuntimeError('Không áp dụng được thiết kế preview: '+run.stderr[-2500:])
        os.replace(output, video_path)
