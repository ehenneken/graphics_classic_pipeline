import sys
import os
import unittest
import importlib

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


if __name__ == '__main__':
    unittest.main()