import sys
import os
import unittest
import importlib
from unittest.mock import MagicMock, patch

PROJECT_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../'))
ROOT = os.path.abspath(os.path.join(PROJECT_HOME, '..'))
sys.path.insert(0, PROJECT_HOME)
sys.path.insert(0, ROOT)


def load_config():
    conf = {}
    base = importlib.import_module('config')
    conf.update({k: v for k, v in vars(base).items() if not k.startswith('_')})
    local_path = os.path.join(ROOT, 'local_config.py')
    if os.path.exists(local_path):
        local = importlib.import_module('local_config')
        conf.update({k: v for k, v in vars(local).items() if not k.startswith('_')})
    return conf


class TestConfig(unittest.TestCase):

    '''Check that config has the necessary entries'''

    def setUp(self):
        self.config = load_config()

    def test_required_config_keys(self):
        '''All required config keys are present'''
        required = ['GRAPHICS_INCLUDE_ARXIV', 'SQLALCHEMY_BINDS']
        missing = [k for k in required if k not in self.config]
        self.assertEqual(missing, [], msg='Missing config keys: %s' % missing)

    def test_api_token_present_with_local_config(self):
        '''GRAPHICS_API_TOKEN is set when local_config.py exists'''
        local_path = os.path.join(ROOT, 'local_config.py')
        if os.path.exists(local_path):
            self.assertIsNotNone(self.config.get('GRAPHICS_API_TOKEN'))


class TestPDFExtraction(unittest.TestCase):

    '''Test PDF image extraction via PyMuPDF using arXiv:2007.10424 as fixture.
    The PDF has one embedded raster image (220x220px, page 1); all other
    figures are vector graphics and are not extracted by get_images().'''

    def setUp(self):
        import tasks
        self.tasks = tasks
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        self.config = {
            'GRAPHICS_MIN_IMAGE_DIMENSION': 100,
            'GRAPHICS_AWS_S3_URL': 'https://s3.amazonaws.com',
            'GRAPHICS_AWS_S3_BUCKET': 'test-bucket',
            'GRAPHICS_SOURCE_NAMES': {'arXiv': 'arXiv'},
        }
        tasks.init(mock_session, self.config)
        self.pdf_path = os.path.join(
            PROJECT_HOME, 'tests/stubdata/arXiv/2007/10424.pdf')

    @patch('tasks.get_boto_session')
    def test_extract_images_from_pdf(self, mock_boto):
        '''Extracts exactly 1 raster image from arXiv:2007.10424'''
        self.assertTrue(os.path.exists(self.pdf_path),
                        'Test PDF missing: %s' % self.pdf_path)
        figures = self.tasks.manage_arXiv_graphics(
            self.pdf_path, 'bibcode', 'arXiv:2007.10424', 'arXiv', dryrun=True)
        self.assertEqual(len(figures), 1)
        fig = figures[0]
        self.assertEqual(fig['figure_id'], 'arxiv2007.10424_f1')
        self.assertEqual(fig['figure_label'], 'figure 1')
        self.assertEqual(fig['figure_caption'], '')
        self.assertEqual(len(fig['images']), 1)
        image = fig['images'][0]
        self.assertEqual(image['format'], 'png')
        self.assertEqual(image['highres'], 'http://arxiv.org/abs/2007.10424')
        self.assertIn('test-bucket', image['thumbnail'])
        self.assertIn('arxiv2007.10424_f1', image['thumbnail'])


if __name__ == '__main__':
    unittest.main()