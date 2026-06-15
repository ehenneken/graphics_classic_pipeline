'''
Module with general file operations:
 - work with TAR files
 - work with graphics files (e.g. convert)
'''
import sys
import os
import re
import tarfile
import magic
import timeout_decorator
from invenio_tools import get_converted_image_name
from PIL import Image


def atoi(text):
    return int(text) if text.isdigit() else text


def natural_keys(text):
    return [atoi(c) for c in re.split(r'(\d+)', text)]


def untar(tar_archive, bibcode, config):
    '''
    Check validity of TAR archive and unpack in temporary directory.
    '''
    tex_files = []
    img_files = []
    try:
        contents = [m.name for m in tarfile.open(tar_archive, 'r:*').getmembers()]
    except Exception:
        contents = []
    contents = [fn for fn in contents if 'orcid' not in fn.lower()]
    TMP_DIR = config.get('GRAPHICS_TMP_DIR')
    extract_dir = "%s/%s" % (TMP_DIR, bibcode)
    t = tarfile.open(tar_archive, 'r:*')
    t.extractall(extract_dir)
    for f in contents:
        extracted_file = "%s/%s" % (extract_dir, f)
        if not os.path.exists(extracted_file):
            sys.stderr.write('File not found: %s\n' % extracted_file)
            continue
        try:
            mtype = magic.from_file(extracted_file)
        except magic.MagicException:
            mtype = 'unknown'
        if mtype.find('TeX') > -1:
            tex_files.append(extracted_file)
        elif (mtype.find('image') > -1 or mtype.find('type EPS') > -1
              or mtype.lower().find('postscript') > -1):
            img_files.append(extracted_file)
        else:
            if extracted_file.lower().split('.')[-1] in ['eps', 'png', 'ps', 'jpg']:
                img_files.append(extracted_file)
    return tex_files, img_files, extract_dir


def convert_images(image_list):
    done_list = []
    remainder = []
    for image in image_list:
        try:
            mtype = magic.from_file(image)
        except magic.MagicException:
            mtype = 'unknown'
        image_name = os.path.split(image)[-1]
        extension = image_name.split('.')[-1].lower()
        if extension.isdigit():
            image_name = image_name.replace('.', '_')
        if mtype.find('PNG') > -1 or extension == 'png':
            done_list.append(image)
            continue
        png_image = get_converted_image_name(image)
        try:
            result = convert_to_png_file(image, png_image)
        except timeout_decorator.timeout_decorator.TimeoutError:
            result = {'status': 'failure', 'file': image, 'reason': 'timeout'}
        if os.path.exists(png_image):
            done_list.append(png_image)
            remainder.append(image)
    done_list.sort(key=natural_keys)
    return remainder, done_list


@timeout_decorator.timeout(15)
def convert_to_png_file(img, png):
    result = {'status': 'success'}
    try:
        Image.open(img).save(png)
    except Exception as e:
        result = {'status': 'failure', 'file': img, 'reason': e}
    return result