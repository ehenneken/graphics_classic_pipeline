from boto3.session import Session


def get_boto_session(config):
    return Session(
        aws_access_key_id=config.get('GRAPHICS_AWS_ACCESS_KEY'),
        aws_secret_access_key=config.get('GRAPHICS_AWS_SECRET_KEY'),
        region_name=config.get('GRAPHICS_AWS_REGION')
    )