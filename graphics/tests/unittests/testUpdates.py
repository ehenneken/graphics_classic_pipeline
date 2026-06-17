import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch

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


class TestIOP(unittest.TestCase):

    def setUp(self):
        self.mock_session = MagicMock()
        (self.mock_session.query.return_value
         .filter.return_value.first.return_value) = get_testdata()
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
        # Record exists and force=False: nothing processed
        res = tasks.process_IOP_graphics(identifiers, False, dryrun=True)
        self.assertIsNone(res)
        # force=True: returns list of (thumbnail, highres) tuples
        res = tasks.process_IOP_graphics(identifiers, True, dryrun=True)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], ('http://thumb1.jpg', 'http://highres1'))
        try:
            os.remove(map_file)
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
