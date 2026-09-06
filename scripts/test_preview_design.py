"""Run with Python from the project root; uses a tiny local FFmpeg fixture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.preview_design import apply_design, read_cues, resize_box, write_ass
from utils.helpers import FFmpegUtils
from PIL import Image, ImageStat, ImageChops
from engine.video_engine import VideoEngine


class DesignTests(unittest.TestCase):
    def test_caption_background_font_single_row_and_blur_order(self):
        import shutil
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            srt = root/'sample.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nChữ mẫu\n',encoding='utf-8')
            base = root/'base.mp4'
            def run(args):
                subprocess.run([FFmpegUtils.ffmpeg_executable(),'-y','-v','error',*args],
                    check=True,**FFmpegUtils.subprocess_kwargs(capture_output=True))
            run(['-f','lavfi','-i','color=c=gray:s=640x360:d=0.2',str(base)])
            design = dict(burn=True,srt=str(srt),sub_size=48,sub_x=.5,sub_y=.5,
                          sub_font='Tahoma',sub_outline=0)
            images = []
            for index,extra in enumerate([{}, {'blur_boxes':[dict(x=.1,y=.1,w=.8,h=.8)]},
                                          {'sub_background':True,'sub_background_opacity':80}]):
                video = root/f'{index}.mp4'
                shutil.copyfile(base,video)
                apply_design(video,dict(design,**extra))
                frame = root/f'{index}.png'
                run(['-i',str(video),'-frames:v','1',str(frame)])
                images.append(Image.open(frame).convert('RGB').copy())
            # On a uniform source, blur-before-text leaves the sharp glyphs intact.
            self.assertLess(sum(ImageStat.Stat(ImageChops.difference(images[0],images[1])).mean),2)
            crop = (200,140,440,220)
            self.assertLess(sum(ImageStat.Stat(images[2].crop(crop)).mean),
                            sum(ImageStat.Stat(images[0].crop(crop)).mean)-30)
            ass = root/'long.ass'
            write_ass(ass,[(0,1,'Một câu rất dài\nvẫn phải nằm trên cùng một hàng phụ đề')],320,640,
                      dict(design,sub_size=200,sub_background=True))
            content = ass.read_text(encoding='utf-8-sig')
            self.assertIn('Style: Default,Tahoma,',content)
            self.assertIn('Backdrop,,',content)
            self.assertNotIn(r'\N',content)
            self.assertNotIn(r'\fs200',content)

    def test_each_title_can_be_hidden_in_render(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            for top,bottom in [(False,False),(True,False),(False,True),(True,True)]:
                vf = VideoEngine._build_title_overlay_filter('Trên' if top else '',
                      'Dưới' if bottom else '',frame_width=640)
                path = Path(folder)/f'{top}_{bottom}.png'
                subprocess.run([FFmpegUtils.ffmpeg_executable(),'-y','-v','error','-f','lavfi',
                                '-i','color=c=gray:s=640x360','-vf',vf,'-frames:v','1',str(path)],
                               check=True,**FFmpegUtils.subprocess_kwargs(capture_output=True))
                image = Image.open(path).convert('RGB')
                self.assertEqual(image.size,(640,360))
                if top:
                    r,g,b = image.getpixel((5,5))
                    self.assertGreater(r,g+100)
                    r,g,b = image.getpixel((5,0))
                    self.assertGreater(r,g+100,'Top title must cover the first pixel row')
                if bottom:
                    r,g,b = image.getpixel((5,image.height-5))
                    self.assertGreater(b,r+100)
                    r,g,b = image.getpixel((5,image.height-1))
                    self.assertGreater(b,r+100,'Bottom title must cover the last pixel row')

    def test_blur_reduces_detail_in_selected_region(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            pattern = Image.new('RGB', (320,240))
            pattern.putdata([(255,255,255) if x%8<4 else (0,0,0)
                             for y in range(240) for x in range(320)])
            pattern.save(root/'pattern.png')
            video = root/'blur.mp4'
            subprocess.run([FFmpegUtils.ffmpeg_executable(), '-y','-v','error','-loop','1',
                            '-i',str(root/'pattern.png'),'-t','0.2','-pix_fmt','yuv420p',str(video)],
                           check=True, **FFmpegUtils.subprocess_kwargs(capture_output=True))
            apply_design(video, {'blur_boxes':[dict(x=.25,y=.25,w=.5,h=.5)]})
            subprocess.run([FFmpegUtils.ffmpeg_executable(), '-y','-v','error','-i',str(video),
                            '-frames:v','1',str(root/'blur.png')],
                           check=True, **FFmpegUtils.subprocess_kwargs(capture_output=True))
            image = Image.open(root/'blur.png').convert('L')
            inside = ImageStat.Stat(image.crop((100,80,220,160))).stddev[0]
            outside = ImageStat.Stat(image.crop((0,0,60,60))).stddev[0]
            self.assertLess(inside, outside*.2)

    def test_corner_resize_and_bounds(self):
        b = dict(x=.1, y=.2, w=.6, h=.4)
        out = resize_box(b, 'br', .15, .1)
        self.assertAlmostEqual(out['w'], .75)
        self.assertAlmostEqual(out['h'], .5)
        for handle in ('tl','tm','tr','ml','mr','bl','bm','br','move'):
            out = resize_box(b, handle, 2, -2)
            self.assertGreaterEqual(out['x'], 0)
            self.assertGreaterEqual(out['y'], 0)
            self.assertLessEqual(out['x']+out['w'], 1.000001)
            self.assertLessEqual(out['y']+out['h'], 1.000001)

    def test_real_composite(self):
        with tempfile.TemporaryDirectory(prefix="preview_kiểm tra_'_", dir=Path.cwd()) as folder:
            root = Path(folder)
            srt = root/'phụ đề.srt'
            srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nXin chào\n', encoding='utf-8-sig')
            self.assertEqual(read_cues(srt)[0][2], 'Xin chào')
            logo = root/'logo.png'
            Image.new('RGBA', (120,60), (255,0,0,255)).save(logo)
            for width, height, bands in [(640,360,False), (360,840,True)]:
                video = root/f'{width}.mp4'
                cmd = [FFmpegUtils.ffmpeg_executable(), '-y', '-v', 'error', '-f', 'lavfi',
                       '-i', f'color=c=gray:s={width}x{height}:r=10:d=1', '-f', 'lavfi',
                       '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)]
                subprocess.run(cmd, check=True, **FFmpegUtils.subprocess_kwargs(capture_output=True))
                if width == 640:
                    ok, error = VideoEngine.process_video_v2(
                        str(video), str(root/'legacy.mp4'), str(video), '', '', '', keep=0, skip=0,
                        logo_path=str(logo), logo_x_ratio=.25, logo_y_ratio=.2, logo_size_pct=20,
                        burn_srt_path=str(srt), burn_sub_x_ratio=.3, burn_sub_y_ratio=.55,
                        delogo_boxes=[dict(x=.1,y=.7,w=.8,h=.1)])
                    self.assertTrue(ok, error)
                design = dict(logo=str(logo), logo_size=20, logo_x=.8, logo_y=.1,
                              burn=True, srt=str(srt), sub_size=28, sub_x=.3, sub_y=.55,
                              blur_boxes=[dict(x=.05,y=.7,w=.9,h=.12), dict(x=.98,y=.98,w=.02,h=.02)])
                apply_design(video, design, bands)
                image_path = root/f'{width}.png'
                subprocess.run([FFmpegUtils.ffmpeg_executable(), '-y', '-v', 'error', '-i', str(video),
                                '-frames:v','1',str(image_path)], check=True,
                               **FFmpegUtils.subprocess_kwargs(capture_output=True))
                image = Image.open(image_path).convert('RGB')
                red = [(x,y) for y in range(height) for x in range(width)
                       if (lambda c: c[0]>180 and c[1]<65 and c[2]<65)(image.getpixel((x,y)))]
                self.assertTrue(red)
                self.assertAlmostEqual(sum(x for x,y in red)/len(red)/width, .8, delta=.015)
                self.assertAlmostEqual(sum(y for x,y in red)/len(red)/height, .1, delta=.015)
                self.assertAlmostEqual((max(x for x,y in red)-min(x for x,y in red))/width, .2, delta=.015)
                # Captions should be centered at the dragged position, in pixels.
                white = [(x,y) for y in range(int(height*.4), int(height*.7)) for x in range(width)
                         if min(image.getpixel((x,y)))>220]
                self.assertTrue(white)
                self.assertAlmostEqual((min(x for x,y in white)+max(x for x,y in white))/2/width, .3, delta=.03)
                self.assertAlmostEqual((min(y for x,y in white)+max(y for x,y in white))/2/height, .55, delta=.03)
                probe = subprocess.run([FFmpegUtils.ffprobe_executable(), '-v','error','-show_streams','-of','json',str(video)],
                                       check=True, **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True))
                streams = json.loads(probe.stdout)['streams']
                self.assertTrue(any(s['codec_type']=='audio' for s in streams))
                self.assertAlmostEqual(float(streams[0]['duration']), 1, delta=.11)
                before = hashlib.sha256(video.read_bytes()).digest()
                with self.assertRaises(ValueError):
                    apply_design(video, {'burn':True,'srt':str(root/'missing.srt')})
                self.assertEqual(before, hashlib.sha256(video.read_bytes()).digest())


if __name__ == '__main__':
    unittest.main()
