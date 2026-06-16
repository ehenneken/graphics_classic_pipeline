import sys
import os
import re
import glob
import random
import urllib.parse
import base64
import json
from operator import itemgetter
from datetime import datetime

import fitz  # PyMuPDF
import simplejson
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm.exc import NoResultFound

from models import GraphicsModel, AlchemyEncoder
from aws_tools import get_boto_session

# Module-level session and config initialised by init()
session = None
config = None


def init(db_session, app_config):
    global session, config
    session = db_session
    config = app_config


graph_link = '<a href="graphics" border=0><img src="%s"></a>'
ADSASS_img = '<img src="%s">'
ADSASS_thmb_img = '<img src="%s" width="100px">'
ADSASS_thmb_link = '<a href="graphics" border=0>%s</a>'
ADS_base_url = 'http://articles.adsabs.harvard.edu/cgi-bin/nph-iarticle_query'
ADS_image_url = (ADS_base_url +
                 '?bibcode=%s&db_key=AST&page_ind=%s&data_type=GIF&type=SCREEN_VIEW')


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_graphics(bibcode):
    try:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == bibcode).one()
        results = simplejson.loads(simplejson.dumps(resp, cls=AlchemyEncoder))
        results['query'] = 'OK'
        session.commit()
    except NoResultFound:
        results = {
            'query': 'failed',
            'error': 'no database entry found for %s' % bibcode}
    except (ValueError, TypeError) as err:
        results = {'query': 'failed', 'error': 'JSON problem (%s)' % err}
        raise
    except Exception as err:
        if 'row' in str(err):
            results = {
                'query': 'failed',
                'error': 'no database entry found for %s' % bibcode}
        else:
            results = {
                'query': 'failed',
                'error': 'PostgreSQL problem (%s)' % err}

    if results and 'figures' in results:
        if len(results['figures']) == 0:
            results['query'] = 'failed'
            results['error'] = 'no data found for %s' % bibcode
            return results
        eprint = results.get('eprint')
        source = results.get('source', 'NA')
        results['ADSlink'] = []
        if not eprint:
            results['figures'] = list(filter(
                lambda a: a['figure_label'] is not None, results['figures']))
        display_figure = random.choice(results['figures'])
        results['pick'] = ''
        results['number'] = 0
        if source in config.get('GRAPHICS_EXTSOURCES', []):
            header = config.get('GRAPHICS_HEADER', {}).get(source, '')
            results['header'] = header
            try:
                display_image = random.choice(display_figure['images'])
                thumb_url = display_image['thumbnail']
                results['pick'] = graph_link % thumb_url
            except Exception:
                pass
            for figure in results['figures']:
                images = figure.get('images', [])
                results['number'] += len(images)
        elif source == 'ADSASS':
            results['header'] = ('Images from the '
                                 '<a href="http://www.adsass.org/" target="_new">'
                                 'ADS All Sky Survey</a>')
            try:
                thumb_img = ADSASS_thmb_img % display_figure['image_url']
                results['pick'] = ADSASS_thmb_link % thumb_img
            except Exception:
                pass
            for figure in results['figures']:
                results['number'] += 1
                results['ADSlink'].append(
                    ADS_image_url % (
                        bibcode.replace('&', '%26'), figure['page'] - 1))
        elif (source.upper() == 'ARXIV'
              and config.get('GRAPHICS_INCLUDE_ARXIV')):
            results['header'] = 'Images extracted from the arXiv e-print'
            try:
                display_image = random.choice(display_figure['images'])
                thumb_url = display_image['highres']
                results['pick'] = graph_link % thumb_url
            except Exception:
                pass
            for figure in results['figures']:
                images = figure.get('images', [])
                results['number'] += len(images)
        elif source.upper() == 'TEST':
            results['pick'] = display_figure
            return results
        else:
            results = {}
    if not results:
        results = {
            'query': 'failed',
            'error': 'no database entry found for %s' % bibcode}
    return results


# ---------------------------------------------------------------------------
# Identifier lookup
# ---------------------------------------------------------------------------

def get_identifiers(bibstem, year, source):
    ids = []
    identifiers = []
    skip_arxiv = [
        '2024arXiv240807974S', '2024arXiv240702360C', '2024arXiv240605306C',
        '2018arXiv180610605K', '2023arXiv230113836H', '2022arXiv220906843S',
        '2023arXiv230103670R', '2022arXiv221005315S', '2022arXiv221109541T',
        '2023arXiv231101438L',
    ]
    if source == 'arXiv':
        q = 'bibstem:arXiv year:%s arxiv_class:(astro-ph.* OR gr-qc)' % year
        fl = 'bibcode, eid'
        idtype = 'eid'
    else:
        q = 'bibstem:"%s" year:%s' % (bibstem, year)
        fl = 'bibcode, identifier, doi'
        idtype = 'identifier'
    solr_args = {'wt': 'json', 'q': q, 'fl': fl, 'rows': 100000}
    headers = {'Authorization': 'Bearer %s' % config.get('GRAPHICS_API_TOKEN', '')}
    response = requests.get(
        config.get('GRAPHICS_SOLR_PATH'),
        params=solr_args,
        headers=headers)
    if response.status_code != 200:
        return []
    resp = response.json()
    for doc in resp['response']['docs']:
        if idtype == 'eid':
            arx_id = doc[idtype]
        else:
            try:
                arx_id = [i for i in doc[idtype] if '/' in i or
                          'arXiv' in i][0]
            except Exception:
                arx_id = None
        doi = doc.get('doi', ['NA'])[0]
        try:
            ids.append({'bibcode': doc['bibcode'], 'arxid': arx_id, 'doi': doi})
        except Exception:
            pass
    if source == 'arXiv' and bibstem != 'arXiv':
        identifiers = [b for b in ids if bibstem in b['bibcode'] and b['arxid']]
    elif source == 'arXiv':
        identifiers = [b for b in ids
                       if b['arxid'] and b['bibcode'] not in skip_arxiv]
    else:
        identifiers = list(ids)
    return identifiers


# ---------------------------------------------------------------------------
# IOP
# ---------------------------------------------------------------------------

def get_thumbnails(d):
    query = {
        'page': 1,
        'show': 200,
        'publicationID': str(d),
    }
    base_url = 'http://www.astroexplorer.org'
    new_id = base64.b64encode(
        json.dumps(query).encode('utf-8')).decode('utf-8').rstrip('=')
    query_url = "{0}/search/{1}".format(base_url, new_id)
    response = requests.get(query_url)
    soup = BeautifulSoup(response.text, "html.parser")
    thumbs = []
    for tag in soup.find_all('div', {'class': "thumbnail-container"}):
        highres = "{0}{1}".format(base_url, tag.a['href'])
        thumb = tag.img['src']
        thumbs.append((thumb, highres))
    return thumbs


def process_IOP_graphics(identifiers, force, dryrun=False):
    doi_pat = re.compile(
        r'<article-id\s+pub-id-type="doi">(?P<doi>.*?)</article-id>')
    print_issn_pat = re.compile(
        r'<issn\ pub-type="ppub">(?P<issn>.{4}-.{4})</issn>',
        re.VERBOSE | re.DOTALL | re.IGNORECASE)
    artid_pat = re.compile(
        r'<elocation-id\ content-type="artnum">(?P<artid>\d+)</elocation-id>')
    volume_pat = re.compile(r'<volume>(?P<volume>\d+)</volume>')
    issue_pat = re.compile(r'<issue>(?P<issue>\d+)</issue>')

    bibcode2fulltext = {}
    map_file = config.get('GRAPHICS_FULLTEXT_MAPS', {}).get('IOP')
    with open(map_file) as fh_map:
        for line in fh_map:
            try:
                bibcode, ft_file, src = line.strip().split('\t')
                if ft_file[-3:].lower() == 'xml':
                    bibcode2fulltext[bibcode] = ft_file
            except Exception:
                continue

    doi2path = {}
    trans_file = config.get('GRAPHICS_FULLTEXT_TRANSLATION', {}).get('IOP')
    if trans_file:
        with open(trans_file) as fh_trans:
            for line in fh_trans:
                try:
                    b, d, ft, t = line.strip().split('\t')
                    doi2path[d] = os.path.dirname(ft).replace(
                        '/proj/ads/articles/sources/STACKS/', '').strip()
                except Exception:
                    continue

    back_file = config.get('GRAPHICS_BACK_DATA_FILE', {}).get('IOP')
    id2thumb = {}
    if back_file and os.path.exists(back_file):
        with open(back_file) as back_data:
            for line in back_data:
                doi, id_, thumb = line.strip().split(',')
                id2thumb[doi] = thumb

    src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('IOP')
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)

    for paper in updates:
        ft_file = bibcode2fulltext.get(paper['bibcode'], None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            continue
        art_path = doi2path.get(paper['doi'], "NA")
        nfigs = manage_IOP_graphics(buffer, paper['bibcode'], paper['doi'],
                                    art_path, src, id2thumb,
                                    update=True, dryrun=dryrun)

    for paper in new:
        ft_file = bibcode2fulltext.get(paper['bibcode'], None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            if ft_file:
                sys.stderr.write('Incorrect full text mapping for %s: %s\n'
                                 % (paper, ft_file))
            else:
                sys.stderr.write('No full text found for %s\n' % paper)
            continue
        art_path = doi2path.get(paper['doi'], "NA")
        try:
            nfigs = manage_IOP_graphics(buffer, paper['bibcode'], paper['doi'],
                                        art_path, src, id2thumb, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing %s (%s)\n' % (paper, e))
            continue
    return nfigs


def manage_IOP_graphics(fulltext, bibcode, DOI, apath, source, id2thumb,
                        update=False, dryrun=False):
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == bibcode).first()
    else:
        graphic = None

    thumbURL = "https://s3.amazonaws.com/aasie/images/%s/%s_tb.%s"
    highURL = "http://www.astroexplorer.org/details/%s"

    thumbnails = []
    try:
        thumbnails = get_thumbnails(DOI)
    except Exception:
        thumbnails = []

    if len(thumbnails) > 0 and not dryrun:
        graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('IOP')
        if update:
            sys.stderr.write('Updating %s\n' % bibcode)
            graphic.source = graph_src
            graphic.figures = []
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % bibcode)
            graphic = GraphicsModel(
                bibcode=bibcode,
                doi=DOI,
                source=graph_src,
                eprint=False,
                figures=[],
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            session.add(graphic)
        session.commit()
    if not dryrun:
        return len(thumbnails)
    else:
        return thumbnails


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

def _arxiv_pdf_path(arx_id, ft_base):
    """Derive the PDF path from an arXiv ID and the base directory."""
    if '/' in arx_id:
        cat = arx_id.split('/')[0]
        year = arx_id.split('/')[1][:2]
        yy = "19%s" % year if int(year) > 80 else "20%s" % year
        aid = arx_id.split('/')[1]
    elif ':' in arx_id:
        cat = 'arXiv'
        yy = arx_id.split(':')[1].split('.')[0]
        aid = arx_id.split(':')[1].split('.')[1]
    else:
        return None, None, None, None
    return "%s/%s/%s/%s.pdf" % (ft_base, cat, yy, aid), cat, yy, aid


def process_arXiv_graphics(identifiers, force, dryrun=False):
    updates = []
    new = []
    ft_base = config.get('GRAPHICS_FULLTEXT_MAPS', {}).get('arXiv')
    for identifier in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == identifier['bibcode']).first()
        if force and resp:
            updates.append(identifier)
        elif not resp:
            new.append(identifier)

    res = None

    for entry in updates:
        pdf_file, cat, yy, aid = _arxiv_pdf_path(entry['arxid'], ft_base)
        if not pdf_file or not os.path.exists(pdf_file):
            sys.stderr.write('PDF not found for %s: %s\n'
                             % (entry['bibcode'], pdf_file))
            continue
        res = manage_arXiv_graphics(pdf_file, entry['bibcode'], entry['arxid'],
                                    cat, dryrun=dryrun, update=True)

    for entry in new:
        pdf_file, cat, yy, aid = _arxiv_pdf_path(entry['arxid'], ft_base)
        if not pdf_file or not os.path.exists(pdf_file):
            sys.stderr.write('PDF not found for %s: %s\n'
                             % (entry['bibcode'], pdf_file))
            continue
        res = manage_arXiv_graphics(pdf_file, entry['bibcode'], entry['arxid'],
                                    cat, dryrun=dryrun)

    return res


def manage_arXiv_graphics(pdf_path, bibcode, arx_id, category,
                           update=False, dryrun=False):
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == bibcode).first()
        if not graphic:
            sys.stderr.write(
                'Note: update for %s, but no existing record found!\n' % bibcode)
    else:
        graphic = None

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        sys.stderr.write('Cannot open PDF for %s: %s\n' % (bibcode, e))
        return None

    if arx_id.find('arXiv') > -1:
        subdir = arx_id.replace('arXiv:', '').split('.')[0]
        eprdir = arx_id.replace('arXiv:', '').split('.')[1]
    else:
        subdir = arx_id.split('/')[1][:4]
        eprdir = arx_id.split('/')[1][4:]

    boto_client = get_boto_session(config).client('s3')
    bucket = config.get('GRAPHICS_AWS_S3_BUCKET')
    min_dim = config.get('GRAPHICS_MIN_IMAGE_DIMENSION', 100)
    image_url = "http://arxiv.org/abs/%s" % arx_id.replace('arXiv:', '')

    figures = []
    fid = 1
    seen_xrefs = set()

    for page_num in range(len(doc)):
        for img in doc[page_num].get_images():
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception as e:
                sys.stderr.write('Cannot extract image xref %s from %s: %s\n'
                                 % (xref, bibcode, e))
                continue

            if pix.width < min_dim or pix.height < min_dim:
                pix = None
                continue

            # Convert CMYK and other non-RGB colorspaces
            if pix.n - pix.alpha >= 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            if arx_id.find('arXiv') > -1:
                figure_id = 'arxiv%s_f%s' % (arx_id.replace('arXiv:', ''), fid)
            else:
                figure_id = '%s_f%s' % (arx_id.replace('/', '_'), fid)

            aws_key = "seri/arXiv/%s/%s/%s/%s.png" % (
                category, subdir, eprdir, figure_id)
            thumb_url = "%s/%s/%s" % (
                config.get('GRAPHICS_AWS_S3_URL'), bucket, aws_key)

            fig_data = {
                'figure_id': figure_id,
                'figure_label': 'figure %s' % fid,
                'figure_caption': '',
                'images': [{
                    'image_id': fid,
                    'format': 'png',
                    'thumbnail': thumb_url,
                    'highres': image_url,
                }],
            }
            figures.append(fig_data)

            if not dryrun:
                try:
                    boto_client.put_object(
                        Key=aws_key,
                        Bucket=bucket,
                        Body=pix.tobytes('png'),
                        ACL='public-read',
                        ContentType='image/png',
                    )
                except Exception as e:
                    sys.stderr.write('Error uploading %s: %s\n' % (aws_key, e))

            pix = None
            fid += 1

    doc.close()

    graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('arXiv')
    thumbnails = [(f['images'][0]['thumbnail'], f['images'][0]['highres'])
                  for f in figures if f.get('images')]
    if figures and not dryrun:
        if update:
            sys.stderr.write('Updating %s\n' % bibcode)
            graphic.source = graph_src
            graphic.figures = figures
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % bibcode)
            try:
                graphic = GraphicsModel(
                    bibcode=bibcode,
                    doi=arx_id,
                    source=graph_src,
                    eprint=True,
                    figures=figures,
                    thumbnails=thumbnails,
                    modtime=datetime.now()
                )
                session.add(graphic)
            except Exception as e:
                sys.stderr.write('Failed adding data for %s: %s\n' % (bibcode, e))
        try:
            session.commit()
        except Exception as e:
            sys.stderr.write('Data commit failed for %s: %s\n' % (bibcode, e))

    if not dryrun:
        return len(figures)
    else:
        return figures


# ---------------------------------------------------------------------------
# Elsevier
# ---------------------------------------------------------------------------

def process_Elsevier_graphics(identifiers, force, dryrun=False):
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)

    for paper in updates:
        nfigs = manage_Elsevier_graphics(paper, update=True, dryrun=dryrun)
    for paper in new:
        try:
            nfigs = manage_Elsevier_graphics(paper, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing %s (%s)\n' % (paper, e))
            continue
    return nfigs


def manage_Elsevier_graphics(record, update=False, dryrun=False):
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None

    thumbURL = "https://ars.els-cdn.com/content/image/%s"
    queryURL = "https://api.elsevier.com/content/object/doi/%s"
    figures = []
    APIkey = config.get('ELSEVIER_API_KEY')
    headers = {'Accept': 'application/json', 'X-ELS-APIKey': APIkey}
    payload = {'view': 'META', 'field': 'url,identifier,description'}
    r = requests.get(queryURL % record.get('doi'), params=payload, headers=headers)
    try:
        PII = r.json()['attachment-metadata-response']['coredata'][
            'dc:identifier'].replace('PII:', '')
    except Exception:
        PII = None
    try:
        thumbs = [t for t in r.json()['attachment-metadata-response']['attachment']
                  if t['type'] == 'IMAGE-THUMBNAIL']
    except Exception:
        thumbs = []

    for thumb in thumbs:
        fig_data = {}
        try:
            fignr = int(re.sub("[^0-9]", "", thumb['ref']))
        except Exception:
            fignr = re.sub("[^0-9]", "", thumb['ref'])
        fig_data['figure_id'] = thumb['eid']
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        highres = ("https://www.sciencedirect.com/science/article/pii/%s" % PII
                   if PII else "https://dx.doi.org/%s" % record['doi'])
        image = {
            'image_id': thumb['eid'],
            'thumbnail': thumbURL % thumb['eid'],
            'format': thumb['mimetype'].split('/')[1],
            'highres': highres,
        }
        fig_data['images'] = [image]
        figures.append(fig_data)
    figures = sorted(figures, key=itemgetter('figure_number'))

    thumbnails = [(f['images'][0]['thumbnail'], f['images'][0]['highres'])
                  for f in figures if f.get('images')]
    if figures and not dryrun:
        graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('Elsevier')
        if update:
            sys.stderr.write('Updating %s\n' % record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            session.add(graphic)
        session.commit()
    if not dryrun:
        return len(figures)
    else:
        return figures


# ---------------------------------------------------------------------------
# EDP (A&A)
# ---------------------------------------------------------------------------

def process_EDP_graphics(identifiers, force, dryrun=False):
    bibcode2fulltext = {}
    map_file = config.get('GRAPHICS_FULLTEXT_MAPS', {}).get('EDP')
    with open(map_file) as fh_map:
        for line in fh_map:
            try:
                bibcode, ft_file, src = line.strip().split('\t')
                if ft_file[-3:].lower() == 'xml':
                    bibcode2fulltext[bibcode] = ft_file
            except Exception:
                continue

    src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('EDP')
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)

    for paper in updates:
        fulltext = bibcode2fulltext.get(paper['bibcode'])
        if not fulltext:
            sys.stderr.write('No full text found for %s (update)\n'
                             % paper['bibcode'])
            continue
        try:
            nfigs = manage_EDP_graphics(paper, fulltext, update=True, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing update %s (%s)\n'
                             % (paper['bibcode'], e))
            continue

    for paper in new:
        fulltext = bibcode2fulltext.get(paper['bibcode'])
        if not fulltext:
            sys.stderr.write('No full text found for %s (new record)\n'
                             % paper['bibcode'])
            continue
        try:
            nfigs = manage_EDP_graphics(paper, fulltext, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing new %s (%s)\n'
                             % (paper['bibcode'], e))
            continue
    return nfigs


def manage_EDP_graphics(record, ft_file, update=False, dryrun=False):
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None

    identifier = os.path.basename(ft_file).replace('.xml', '')
    srcdir = config.get('GRAPHICS_GRAPHICS_LOCATION', {}).get('EDP')
    thumbs = glob.glob('%s/%s/*.jpg' % (srcdir, identifier))
    thumbs = [t for t in thumbs
              if t.lower().find('fig') > -1 and t.lower().find('small') == -1]

    bucket = config.get('GRAPHICS_AWS_S3_BUCKET')
    volno = record['bibcode'][9:13].replace('.', '0')
    thumb_bucket = "seri/A+A/%s/%s" % (volno, identifier)
    boto_client = get_boto_session(config).client('s3')
    mimetype = 'image/jpeg'
    figures = []
    for thumb in thumbs:
        fig_data = {}
        try:
            fignr = int(re.sub(r'^.*fig(\d+).*', r'\1', os.path.basename(thumb)))
        except Exception:
            fignr = 0
        fig_data['figure_id'] = re.sub(r'^(.*)\..*', r'\1', os.path.basename(thumb))
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        highres = "http://dx.doi.org/%s" % record['doi']
        key = "%s/%s" % (thumb_bucket, os.path.basename(thumb))
        thumbURL = "%s/%s/%s" % (
            config.get('GRAPHICS_AWS_S3_URL'), bucket,
            urllib.parse.quote(key))
        image = {
            'image_id': re.sub(r'^(.*)\..*', r'\1', os.path.basename(thumb)),
            'thumbnail': thumbURL,
            'format': mimetype.split('/')[1],
            'highres': highres,
        }
        fig_data['images'] = [image]
        figures.append(fig_data)
        try:
            with open(thumb, 'rb') as data:
                boto_client.put_object(Key=key, Bucket=bucket, Body=data,
                                       ACL='public-read', ContentType=mimetype)
        except Exception as e:
            sys.stderr.write('Error loading image data for %s: %s\n' % (thumb, e))
            continue

    figures = sorted(figures, key=itemgetter('figure_number'))
    thumbnails = [(f['images'][0]['thumbnail'], f['images'][0]['highres'])
                  for f in figures if f.get('images')]
    if figures and not dryrun:
        graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('EDP')
        if update:
            sys.stderr.write('Updating %s\n' % record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            session.add(graphic)
        session.commit()


# ---------------------------------------------------------------------------
# OUP (MNRAS)
# ---------------------------------------------------------------------------

def process_OUP_graphics(identifiers, force, dryrun=False):
    src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('OUP')
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)

    for paper in updates:
        try:
            nfigs = manage_OUP_graphics(paper, update=True, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing update %s (%s)\n'
                             % (paper['bibcode'], e))
            continue
    for paper in new:
        try:
            nfigs = manage_OUP_graphics(paper, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing new %s (%s)\n'
                             % (paper['bibcode'], e))
            continue
    return nfigs


def manage_OUP_graphics(record, update=False, dryrun=False):
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None

    identifier = record['doi'].split('/')[-1]
    volume = record['bibcode'][9:13].replace('.', '').strip()
    srcdir = config.get('GRAPHICS_GRAPHICS_LOCATION', {}).get('OUP')
    thumbs = glob.glob('%s/%s/%s/*.jpeg' % (srcdir, volume, identifier))

    bucket = config.get('GRAPHICS_AWS_S3_BUCKET')
    volno = record['bibcode'][9:13].replace('.', '0')
    thumb_bucket = "seri/MNRAS/%s/%s" % (volno, identifier)
    boto_client = get_boto_session(config).client('s3')
    mimetype = 'image/jpeg'
    figures = []
    for thumb in thumbs:
        fig_data = {}
        try:
            fignr = (os.path.basename(thumb)
                     .replace(identifier, '').replace('fig', '')
                     .replace('.jpeg', '').strip())
        except Exception:
            fignr = "0"
        fig_data['figure_id'] = re.sub(r'^(.*)\..*', r'\1', os.path.basename(thumb))
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        highres = "http://dx.doi.org/%s" % record['doi']
        key = "%s/%s" % (thumb_bucket, os.path.basename(thumb))
        thumbURL = "%s/%s/%s" % (
            config.get('GRAPHICS_AWS_S3_URL'), bucket,
            urllib.parse.quote(key))
        image = {
            'image_id': re.sub(r'^(.*)\..*', r'\1', os.path.basename(thumb)),
            'thumbnail': thumbURL,
            'format': mimetype.split('/')[1],
            'highres': highres,
        }
        fig_data['images'] = [image]
        figures.append(fig_data)
        try:
            with open(thumb, 'rb') as data:
                boto_client.put_object(Key=key, Bucket=bucket, Body=data,
                                       ACL='public-read', ContentType=mimetype)
        except Exception as e:
            sys.stderr.write('Error loading image data for %s: %s\n' % (thumb, e))
            continue

    figures = sorted(figures, key=itemgetter('figure_number'))
    thumbnails = [(f['images'][0]['thumbnail'], f['images'][0]['highres'])
                  for f in figures if f.get('images')]
    if figures and not dryrun:
        graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('OUP')
        if update:
            sys.stderr.write('Updating %s\n' % record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            session.add(graphic)
        session.commit()


# ---------------------------------------------------------------------------
# APS
# ---------------------------------------------------------------------------

def process_APS_graphics(identifiers, force, dryrun=False):
    figure_pat = re.compile(r'<fig\ (?P<figno>.*?)>')
    src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('APS')
    bibcode2fulltext = {}
    map_file = config.get('GRAPHICS_FULLTEXT_MAPS', {}).get('APS')
    with open(map_file) as fh_map:
        for line in fh_map:
            bibcode, ft_file, source = line.strip().split('\t')
            if ft_file.endswith('.xml'):
                bibcode2fulltext[bibcode] = ft_file

    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)

    for paper in updates:
        ft_file = bibcode2fulltext.get(paper, None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            continue
        match = figure_pat.findall(buffer)
        figure_num = len(match)
        try:
            nfigs = manage_APS_graphics(paper, figure_num, update=True, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing update %s (%s)\n'
                             % (paper['bibcode'], e))
            continue

    for paper in new:
        ft_file = bibcode2fulltext.get(paper['bibcode'], None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            continue
        match = figure_pat.findall(buffer)
        figure_num = len(match)
        try:
            nfigs = manage_APS_graphics(paper, figure_num, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing new %s (%s)\n'
                             % (paper['bibcode'], e))
            continue
    return nfigs


def manage_APS_graphics(record, num_figs, update=False, dryrun=False):
    thumbURL = "https://journals.aps.org/prd/article/%s/figures/%s/small"
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None

    figures = []
    for i in range(1, num_figs + 1):
        fig_data = {
            'figure_id': "Figure %s" % i,
            'figure_label': "Figure %s" % i,
            'figure_caption': '',
            'figure_number': i,
            'images': [{
                'image_id': i,
                'thumbnail': thumbURL % (record['doi'], i),
                'format': 'jpeg',
                'highres': "http://dx.doi.org/%s" % record['doi'],
            }],
        }
        figures.append(fig_data)
    figures = sorted(figures, key=itemgetter('figure_number'))

    thumbnails = [(f['images'][0]['thumbnail'], f['images'][0]['highres'])
                  for f in figures if f.get('images')]
    if figures and not dryrun:
        graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('APS')
        if update:
            sys.stderr.write('Updating %s\n' % record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            session.add(graphic)
        session.commit()


# ---------------------------------------------------------------------------
# Annual Reviews
# ---------------------------------------------------------------------------

def process_AnnRev_graphics(identifiers, force, dryrun=False):
    src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('AnnRev')
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)

    for paper in updates:
        try:
            nfigs = manage_AnnRev_graphics(paper, update=True, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing update %s (%s)\n'
                             % (paper['bibcode'], e))
            continue
    for paper in new:
        try:
            nfigs = manage_AnnRev_graphics(paper, dryrun=dryrun)
        except Exception as e:
            sys.stderr.write('Error processing new %s (%s)\n'
                             % (paper['bibcode'], e))
            continue
    return nfigs


def manage_AnnRev_graphics(record, update=False, dryrun=False):
    if update:
        graphic = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None

    srcdir = config.get('GRAPHICS_GRAPHICS_LOCATION', {}).get('AnnRev')
    graph_file = '%s/images/%s.json' % (
        srcdir, record['bibcode'].replace('&', '+'))
    try:
        thumbs = json.loads(open(graph_file).read())['images']
    except Exception:
        thumbs = []

    mimetype = 'image/gif'
    figures = []
    for thumb in thumbs:
        fig_data = {}
        try:
            fignr = os.path.basename(thumb).split('.')[1].replace('f', '').strip()
        except Exception:
            fignr = "0"
        fig_data['figure_id'] = (re.sub(r'^(.*)\..*', r'\1', os.path.basename(thumb))
                                 .replace('.gif', ''))
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        image = {
            'image_id': re.sub(r'^(.*)\..*', r'\1', os.path.basename(thumb)),
            'thumbnail': thumb,
            'format': mimetype.split('/')[1],
            'highres': "http://dx.doi.org/%s" % record['doi'],
        }
        fig_data['images'] = [image]
        figures.append(fig_data)

    figures = sorted(figures, key=itemgetter('figure_number'))
    thumbnails = [(f['images'][0]['thumbnail'], f['images'][0]['highres'])
                  for f in figures if f.get('images')]
    if figures and not dryrun:
        graph_src = config.get('GRAPHICS_SOURCE_NAMES', {}).get('AnnRev')
        if update:
            sys.stderr.write('Updating %s\n' % record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n' % record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            session.add(graphic)
        session.commit()