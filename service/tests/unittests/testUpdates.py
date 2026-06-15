import sys
import os
import shutil
import json
import unittest
from unittest.mock import MagicMock
from datetime import datetime

PROJECT_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../'))
sys.path.insert(0, PROJECT_HOME)

from models import GraphicsModel
import tasks


def get_testdata():
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


@unittest.skip("skip update testing (IOP)")
class TestIOP(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        (self.mock_session.query.return_value
         .filter.return_value.first.return_value) = get_testdata()
        self.config = {
            'GRAPHICS_TMP_DIR': "%s/tests/stubdata" % PROJECT_HOME,
            'GRAPHICS_ENABLE_UPDATES': False,
            'GRAPHICS_FULLTEXT_MAPS': {},
            'GRAPHICS_FULLTEXT_TRANSLATION': {},
            'GRAPHICS_BACK_DATA_FILE': {},
            'GRAPHICS_SOURCE_NAMES': {'IOP': 'IOP', 'arXiv': 'arXiv'},
        }
        tasks.init(self.mock_session, self.config)

    def test_config_values(self):
        '''Required IOP config keys are present and non-empty'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        self.assertTrue(self.config.get('GRAPHICS_FULLTEXT_MAPS'))
        self.assertTrue(self.config['GRAPHICS_FULLTEXT_MAPS'].get('IOP'))
        self.assertTrue(self.config['GRAPHICS_FULLTEXT_MAPS'].get('arXiv'))
        self.assertTrue(self.config.get('GRAPHICS_BACK_DATA_FILE'))
        self.assertTrue(self.config['GRAPHICS_BACK_DATA_FILE'].get('IOP'))
        self.assertTrue(self.config['GRAPHICS_SOURCE_NAMES'].get('IOP'))
        self.assertTrue(self.config['GRAPHICS_SOURCE_NAMES'].get('arXiv'))

    def test_IOP_update(self):
        '''IOP processor skips existing records without force, updates with force'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        identifiers = [{'bibcode': '2013ApJ...778L..42P',
                        'arxid': 'arXiv:1311.1201'}]
        map_file = "%s/tests/stubdata/IOP_ft.map" % PROJECT_HOME
        ft_file = "%s/tests/stubdata/stubdata.xml" % PROJECT_HOME
        with open(map_file, 'w') as f:
            f.write("2013ApJ...778L..42P\t%s\tIOP" % ft_file)
        self.config['GRAPHICS_FULLTEXT_MAPS']['IOP'] = map_file
        # Without force: record exists, nothing processed
        res = tasks.process_IOP_graphics(identifiers, False, dryrun=True)
        self.assertIsNone(res)
        # With force: should process and return figure list
        res = tasks.process_IOP_graphics(identifiers, True, dryrun=True)
        self.assertEqual(len(res), 3)
        figures = [f['figure_label'] for f in res]
        self.assertEqual(figures, ['Figure 1.', 'Figure 2.', 'Figure 3.'])
        try:
            os.remove(map_file)
        except OSError:
            pass


@unittest.skip("skip update testing (arXiv)")
class TestARXIV(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        (self.mock_session.query.return_value
         .filter.return_value.first.return_value) = None
        self.config = {
            'GRAPHICS_TMP_DIR': "%s/tests/stubdata" % PROJECT_HOME,
            'GRAPHICS_IMAGE_DIR': "%s/tests/stubdata/graphics" % PROJECT_HOME,
            'GRAPHICS_ENABLE_UPDATES': False,
            'GRAPHICS_FULLTEXT_MAPS': {},
            'GRAPHICS_SOURCE_NAMES': {'arXiv': 'arXiv'},
            'GRAPHICS_AWS_S3_URL': 'https://s3.amazonaws.com',
            'GRAPHICS_AWS_S3_BUCKET': 'test-bucket',
        }
        tasks.init(self.mock_session, self.config)

    def test_ARXIV_update(self):
        '''arXiv processor extracts the expected number of figures'''
        if not self.config.get('GRAPHICS_ENABLE_UPDATES', False):
            return True
        identifiers = [{'bibcode': 'bibcode', 'arxid': 'arXiv:YY.NN'}]
        self.config['GRAPHICS_FULLTEXT_MAPS']['arXiv'] = (
            "%s/tests/stubdata" % PROJECT_HOME)
        res = tasks.process_arXiv_graphics(identifiers, False, dryrun=True)
        self.assertEqual(len(res), 9)
        expected_ids = ['arxivYY.NN_f%s' % i for i in range(1, 10)]
        self.assertEqual([f['figure_id'] for f in res], expected_ids)
        try:
            shutil.rmtree(self.config['GRAPHICS_IMAGE_DIR'])
        except Exception:
            pass


if __name__ == '__main__':
    unittest.main(verbosity=2)