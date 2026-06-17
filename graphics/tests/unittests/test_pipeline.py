import sys
import os
import json
import unittest
import importlib
from unittest.mock import MagicMock, patch
from datetime import datetime

PROJECT_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../'))
ROOT = os.path.abspath(os.path.join(PROJECT_HOME, '..'))
sys.path.insert(0, PROJECT_HOME)
sys.path.insert(0, ROOT)

from models import GraphicsModel
import tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_CONFIG = {
    'GRAPHICS_EXTSOURCES': ['IOP', 'Elsevier', 'EDP'],
    'GRAPHICS_HEADER': {},
    'GRAPHICS_INCLUDE_ARXIV': True,
}


def get_query_testdata():
    figures = [{"images": [{"image_id": "fg1", "format": "gif",
                             "thumbnail": "fg1_thumb_url",
                             "highres": "fg1_highres_url"}],
                "figure_caption": "Figure 1",
                "figure_label": "Figure 1",
                "figure_id": "fg1"}]
    return GraphicsModel(
        bibcode='9999BBBBBVVVVQPPPPI',
        doi='DOI',
        source='TEST',
        eprint=False,
        figures=figures,
        modtime=datetime.now()
    )


def get_iop_testdata():
    dfile = "%s/tests/stubdata/IOPstubdata.json" % PROJECT_HOME
    with open(dfile) as data_file:
        data = json.load(data_file)
    return GraphicsModel(
        bibcode=data['bibcode'],
        doi=data['doi'],
        source=data['source'],
        eprint=data['eprint'],
        figures=data['figures'],
        modtime=data['modtime']
    )


def load_config():
    conf = {}
    base = importlib.import_module('config')
    conf.update({k: v for k, v in vars(base).items() if not k.startswith('_')})
    local_path = os.path.join(ROOT, 'local_config.py')
    if os.path.exists(local_path):
        local = importlib.import_module('local_config')
        conf.update({k: v for k, v in vars(local).items() if not k.startswith('_')})
    return conf


# ---------------------------------------------------------------------------
# Query tests (was testEndpoint.py)
# ---------------------------------------------------------------------------

class TestExpectedResults(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        tasks.init(self.mock_session, BASE_CONFIG)
        (self.mock_session.query.return_value
         .filter.return_value.one.return_value) = get_query_testdata()

    def test_query(self):
        '''Query with a known bibcode returns the expected figures and bibcode'''
        results = tasks.get_graphics('9999BBBBBVVVVQPPPPI')
        self.assertEqual(results['query'], 'OK')
        self.assertEqual(results['bibcode'], '9999BBBBBVVVVQPPPPI')
        expected_figures = [{"images": [{"image_id": "fg1", "format": "gif",
                                         "thumbnail": "fg1_thumb_url",
                                         "highres": "fg1_highres_url"}],
                             "figure_caption": "Figure 1",
                             "figure_label": "Figure 1",
                             "figure_id": "fg1"}]
        self.assertEqual(results['figures'], expected_figures)

    def test_pick_is_set_for_test_source(self):
        '''For source=TEST the pick field is the chosen figure dict'''
        results = tasks.get_graphics('9999BBBBBVVVVQPPPPI')
        self.assertIsNotNone(results.get('pick'))


class TestDatabaseError(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        tasks.init(self.mock_session, BASE_CONFIG)
        (self.mock_session.query.return_value
         .filter.return_value.one.side_effect) = Exception('db connection lost')

    def test_query(self):
        '''A database exception results in a failed query response'''
        results = tasks.get_graphics('9999BBBBBVVVVQPPPPI')
        self.assertEqual(results['query'], 'failed')
        self.assertIn('error', results)


class TestJSONError(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        tasks.init(self.mock_session, BASE_CONFIG)
        (self.mock_session.query.return_value
         .filter.return_value.one.return_value) = 'not-a-model'

    def test_query(self):
        '''Non-model DB return value results in a failed query response'''
        results = tasks.get_graphics('9999BBBBBVVVVQPPPPI')
        self.assertEqual(results['query'], 'failed')
        self.assertIn('error', results)


class TestNoDataReturned(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        tasks.init(self.mock_session, BASE_CONFIG)
        g = GraphicsModel(
            bibcode='9999BBBBBVVVVQPPPPI',
            doi='DOI',
            source='TEST',
            eprint=False,
            figures=[],
            modtime=datetime.now()
        )
        (self.mock_session.query.return_value
         .filter.return_value.one.return_value) = g

    def test_query(self):
        '''An empty figures list returns a failed query with an error message'''
        results = tasks.get_graphics('9999BBBBBVVVVQPPPPI')
        self.assertEqual(results['query'], 'failed')
        self.assertIn('error', results)


# ---------------------------------------------------------------------------
# Config and PDF extraction tests (was testInternals.py)
# ---------------------------------------------------------------------------

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

    '''PDF image extraction via PyMuPDF using arXiv:2007.10424 as fixture.
    The PDF has one embedded raster image (220x220px, page 1); all other
    figures are vector graphics and are not extracted by get_images().'''

    def setUp(self):
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
        figures = tasks.manage_arXiv_graphics(
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


# ---------------------------------------------------------------------------
# Publisher processing tests (was testUpdates.py)
# ---------------------------------------------------------------------------

class TestIOP(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        (self.mock_session.query.return_value
         .filter.return_value.first.return_value) = get_iop_testdata()
        self.config = {
            'GRAPHICS_FULLTEXT_MAPS': {},
            'GRAPHICS_FULLTEXT_TRANSLATION': {},
            'GRAPHICS_BACK_DATA_FILE': {},
            'GRAPHICS_SOURCE_NAMES': {'IOP': 'IOP'},
        }
        tasks.init(self.mock_session, self.config)

    @patch('tasks.get_thumbnails')
    def test_IOP_update(self, mock_thumbnails):
        '''IOP processor skips existing records without force, updates with force'''
        mock_thumbnails.return_value = [
            ('http://thumb1.jpg', 'http://highres1'),
            ('http://thumb2.jpg', 'http://highres2'),
            ('http://thumb3.jpg', 'http://highres3'),
        ]
        identifiers = [{'bibcode': '2013ApJ...778L..42P',
                        'arxid': 'arXiv:1311.1201',
                        'doi': '10.3847/2041-8213/abc4f7'}]
        map_file = "%s/tests/stubdata/IOP_ft.map" % PROJECT_HOME
        ft_file = "%s/tests/stubdata/stubdata.xml" % PROJECT_HOME
        with open(map_file, 'w') as f:
            f.write("2013ApJ...778L..42P\t%s\tIOP" % ft_file)
        self.config['GRAPHICS_FULLTEXT_MAPS']['IOP'] = map_file
        res = tasks.process_IOP_graphics(identifiers, False, dryrun=True)
        self.assertIsNone(res)
        res = tasks.process_IOP_graphics(identifiers, True, dryrun=True)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], ('http://thumb1.jpg', 'http://highres1'))
        try:
            os.remove(map_file)
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
