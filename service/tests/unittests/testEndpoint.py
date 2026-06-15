import sys
import os
import unittest
from unittest.mock import MagicMock
from datetime import datetime

PROJECT_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../'))
sys.path.insert(0, PROJECT_HOME)

from models import GraphicsModel
import tasks


def get_testdata():
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


BASE_CONFIG = {
    'GRAPHICS_EXTSOURCES': ['IOP', 'Elsevier', 'EDP'],
    'GRAPHICS_HEADER': {},
    'GRAPHICS_INCLUDE_ARXIV': True,
}


class TestExpectedResults(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        tasks.init(self.mock_session, BASE_CONFIG)
        (self.mock_session.query.return_value
         .filter.return_value.one.return_value) = get_testdata()

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
        # Returning a plain string causes a TypeError when we try results['query'] = 'OK'
        (self.mock_session.query.return_value
         .filter.return_value.one.return_value) = 'not-a-model'

    def test_query(self):
        '''Malformed DB return value raises an exception'''
        with self.assertRaises(Exception):
            tasks.get_graphics('9999BBBBBVVVVQPPPPI')


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


if __name__ == '__main__':
    unittest.main(verbosity=2)