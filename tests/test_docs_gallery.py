"""Keep README screenshots present, readable-sized and explicitly illustrative."""
import re
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {'realtime-subtitles.jpg', 'proactive-concepts.jpg',
          'selection-qa.jpg', 'floating-window.jpg'}


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
        refs = re.findall(r'docs/images/([a-z-]+\.jpg)', text)
        self.assertEqual(len(refs), 4)
        self.assertEqual(set(refs), IMAGES)
        for name in refs:
            self.assertTrue((ROOT / 'docs/images' / name).is_file(), name)

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
        gallery = text.split('## 界面预览', 1)[1].split('## 五分钟启动', 1)[0]
        self.assertIn('独立演示数据', gallery)
        self.assertIn('不代表实时模型输出、延迟或识别准确率测试', gallery)
        for heading in ['实时字幕', '主动解释新概念', '划线提问', 'macOS 悬浮窗']:
            self.assertIn('### ' + heading, gallery)
