"""Keep README screenshots present, readable-sized and explicitly illustrative."""
import struct
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {'realtime-subtitles.jpg', 'proactive-concepts.jpg',
          'selection-qa.jpg', 'floating-window.jpg'}


class GalleryHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.rows = []
        self.images = []
        self.row = None
        self.cell = None
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.row = []
            self.rows.append(self.row)
        elif tag == 'td' and self.row is not None:
            self.cell = {'attrs': attrs, 'images': []}
            self.row.append(self.cell)
        elif tag == 'a':
            self.links.append(attrs.get('href'))
        elif tag == 'img' and attrs.get('src', '').startswith('docs/images/'):
            image = {**attrs, 'href': self.links[-1] if self.links else None}
            self.images.append(image)
            if self.cell is not None:
                self.cell['images'].append(image)

    def handle_endtag(self, tag):
        if tag == 'tr':
            self.row = None
        elif tag == 'td':
            self.cell = None
        elif tag == 'a' and self.links:
            self.links.pop()


def jpeg_size(data):
    if not data.startswith(b'\xff\xd8'):
        raise ValueError('Not a JPEG')
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 255:
            raise ValueError('Invalid JPEG marker')
        marker = data[offset + 1]
        offset += 2
        length = struct.unpack('>H', data[offset:offset + 2])[0]
        if length < 2 or offset + length > len(data):
            raise ValueError('Truncated JPEG')
        if marker in {0xC0, 0xC1, 0xC2}:
            height, width = struct.unpack('>HH', data[offset + 3:offset + 7])
            return width, height
        offset += length
    raise ValueError('Missing JPEG dimensions')


class GalleryTests(unittest.TestCase):
    def test_four_readme_links_resolve(self):
        text = (ROOT / 'README.md').read_text()
        images = GalleryHTML(text).images
        self.assertEqual(len(images), 4)
        self.assertEqual({Path(i['src']).name for i in images}, IMAGES)
        for image in images:
            self.assertTrue((ROOT / image['src']).is_file(), image['src'])
            self.assertEqual(image['href'], image['src'] + '?raw=true')
            self.assertTrue(image.get('alt', '').strip())

    def test_gallery_is_two_rows_by_two_columns(self):
        parsed = GalleryHTML((ROOT / 'README.md').read_text())
        self.assertEqual([len(row) for row in parsed.rows], [2, 2])
        for row in parsed.rows:
            for cell in row:
                self.assertEqual(cell['attrs'].get('width'), '50%')
                self.assertEqual(cell['attrs'].get('valign'), 'top')
                self.assertEqual(len(cell['images']), 1)
                self.assertEqual(cell['images'][0].get('width'), '100%')

    def test_images_are_nonempty_jpegs_at_readable_sizes(self):
        for name in IMAGES:
            data = (ROOT / 'docs/images' / name).read_bytes()
            self.assertGreater(len(data), 10000, name)
            self.assertLess(len(data), 1000000, name)
            width, height = jpeg_size(data)
            self.assertGreaterEqual(width, 640, name)
            self.assertGreaterEqual(height, 480, name)

    def test_gallery_is_labeled_as_demo_not_performance_evidence(self):
        text = (ROOT / 'README.md').read_text()
        gallery = text.split('## 界面预览', 1)[1].split('## 主动解释与个性化学习建议', 1)[0]
        self.assertIn('独立演示数据', gallery)
        self.assertIn('不代表实时模型输出、延迟或识别准确率测试', gallery)
        for heading in ['实时字幕', '主动解释新概念', '划线提问', 'macOS 悬浮窗']:
            self.assertIn('<h3>' + heading + '</h3>', gallery)

    def test_memory_copy_distinguishes_records_from_mastery(self):
        text = (ROOT / 'README.md').read_text()
        memory = text.split('## 主动解释与个性化学习建议', 1)[1].split('## 五分钟启动', 1)[0]
        for phrase in ['具体卡点', '学习原文出处', '后续问题', '非用户真实记录',
                       '曾讨论、学习过、复习过', '不等于已经掌握',
                       'memory_recall_enabled=false', 'tmcra_write=false']:
            self.assertIn(phrase, memory)
