import requests


def get_client(config):
    session = requests.Session()
    token = config.get('GRAPHICS_API_TOKEN')
    if token:
        session.headers.update({'Authorization': 'Bearer %s' % token})
    return session