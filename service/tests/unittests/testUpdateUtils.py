import sys
import os
import shutil
import unittest
import time
import timeout_decorator

PROJECT_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../'))
sys.path.insert(0, PROJECT_HOME)


@timeout_decorator.timeout(2)
def _timed_sleep(s):
    time.sleep(s)
    return s


@unittest.skip("skip update testing (file operations)")
class TestFileOps(unittest.TestCase):

    def setUp(self):
        self.config = {
            'GRAPHICS_TMP_DIR': "%s/tests/stubdata" % PROJECT_HOME,
            'GRAPHICS_ENABLE_UPDATES': False,
        }

    def test_timeout(self):
        '''Timeout decorator raises on slow calls and passes on fast ones'''
        try:
            res = _timed_sleep(1)
        except timeout_decorator.timeout_decorator.TimeoutError:
            res = 'timeout'
        self.assertEqual(res, 1)
        try:
            res = _timed_sleep(3)
        except timeout_decorator.timeout_decorator.TimeoutError:
            res = 'timeout'
        self.assertEqual(res, 'timeout')

    def test_graphics_converter(self):
        '''PNG converter produces a valid PNG file'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        from file_ops import convert_to_png_file
        import magic
        img = "%s/tests/stubdata/test_image.jpg" % PROJECT_HOME
        png = "%s/tests/stubdata/test_image_out.png" % PROJECT_HOME
        convert_to_png_file(img, png)
        self.assertTrue(os.path.exists(png))
        self.assertIn('PNG', magic.from_file(png))
        os.remove(png)

    def test_unpack_archive(self):
        '''TAR archive unpacks to expected TeX and image files'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        from file_ops import untar
        archive = "%s/tests/stubdata/arXiv/YY/NN.tar.gz" % PROJECT_HOME
        self.assertTrue(os.path.exists(archive))
        tex, imgs, sdir = untar(archive, 'NN', self.config)
        expected_dir = "%s/tests/stubdata/NN" % PROJECT_HOME
        self.assertEqual(sdir, expected_dir)
        imgs_expected = ['figure0%s.ps' % i for i in range(1, 10)]
        self.assertEqual([os.path.basename(i) for i in imgs], imgs_expected)
        tex_expected = ['2_m51_eng.tex', 'sao1.sty']
        self.assertEqual([os.path.basename(t) for t in tex], tex_expected)
        shutil.rmtree(sdir)

    def test_convert_images(self):
        '''Image converter produces PNG files for all source images'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        from file_ops import untar, convert_images
        import magic
        archive = "%s/tests/stubdata/arXiv/YY/NN.tar.gz" % PROJECT_HOME
        tex, imgs, sdir = untar(archive, 'NN', self.config)
        remainder, converted_images = convert_images(imgs)
        self.assertEqual(len(imgs), len(remainder))
        imgs_expected = ['figure0%s.png' % i for i in range(1, 10)]
        self.assertEqual([os.path.basename(i) for i in converted_images],
                         imgs_expected)
        self.assertTrue(all(magic.from_file(i).find('PNG') > -1
                            for i in converted_images))
        shutil.rmtree(sdir)

    def test_extract_captions(self):
        '''Caption extractor pulls expected data from TeX source'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        from file_ops import untar, convert_images
        from invenio_tools import extract_captions, prepare_image_data, extract_context
        archive = "%s/tests/stubdata/arXiv/YY/NN.tar.gz" % PROJECT_HOME
        tex, imgs, sdir = untar(archive, 'NN', self.config)
        remainder, converted_images = convert_images(imgs)
        tex_file = [f for f in tex if f.split('.')[-1] == 'tex'][0]
        TMP = self.config['GRAPHICS_TMP_DIR']
        im_data = extract_captions(tex_file, TMP, converted_images)
        self.assertEqual(im_data[0], ('', 'noimgDistance to M~51', ''))
        cleaned = prepare_image_data(im_data, tex_file, converted_images)
        self.assertEqual(os.path.basename(cleaned[-1][0]), 'figure09.png')
        self.assertEqual(cleaned[-1][2], '')
        context = extract_context(tex_file, cleaned)
        self.assertEqual(context[0], ('', 'noimgDistance to M~51', '', []))
        shutil.rmtree(sdir)


if __name__ == '__main__':
    unittest.main(verbosity=2)