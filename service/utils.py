import os
import re
import sys
import glob
import shutil
import commands
import urllib
from operator import itemgetter
import requests
from flask import current_app, request
from client import client
from models import db, GraphicsModel
import file_ops
from datetime import datetime
from invenio_tools import extract_captions, prepare_image_data,\
    extract_context, remove_dups
from aws_tools import get_boto_session
import json
import base64
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings()

AAS = ['ApJS.','ApJ..','AJ...', 'RNAAS','PSJ..']
IOPscience = ['PASP.','CQGra']

def get_identifiers(bibstem, year, source):
    """
    :param bibstem:
    :param year:
    :param arXiv:
    :return:
    """
    ids = []
    identifiers = []
    skip_arxiv = ['2024arXiv240807974S','2024arXiv240702360C','2024arXiv240605306C','2018arXiv180610605K','2023arXiv230113836H','2022arXiv220906843S','2023arXiv230103670R','2022arXiv221005315S','2022arXiv221109541T','2023arXiv231101438L']
    # In the case of arXiv we get the general bibstem arXiv and filter
    # later on the actual bibstem (if this is other than "arXiv")
    if source == 'arXiv':
#        q = 'pub:"ArXiv e-prints" year:%s' % year
#        q = 'bibstem:arXiv year:%s arxiv_class:astrophysics' % year
        q = 'bibstem:arXiv year:%s arxiv_class:(astro-ph.* OR gr-qc)' % year
#        q = 'bibstem:arXiv year:%s arxiv_class:"general relativity"' % year
        fl= 'bibcode, eid'
        idtype = 'eid'
    else:
        q = 'bibstem:"%s" year:%s' % (bibstem, year)
        fl= 'bibcode, identifier, doi'
        idtype = 'identifier'
    solr_args = {'wt': 'json',
                 'q': q,
                 'fl': fl,
                 'rows': 100000}
    headers = {'X-Forwarded-Authorization':
               request.headers.get('Authorization')}
    response = client().get(
        current_app.config.get("GRAPHICS_SOLR_PATH"),
        params=solr_args, headers=headers)
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
            except:
                arx_id = None
        doi = doc.get('doi',['NA'])[0]
        try:
            ids.append({'bibcode': doc['bibcode'], 'arxid': arx_id, 'doi':doi})
        except:
            pass
    if source == 'arXiv' and bibstem != 'arXiv':
        identifiers = [b for b in ids if bibstem in b['bibcode'] and
                       b['arxid']]
    elif source == 'arXiv':
        identifiers = [b for b in ids if b['arxid'] and b['bibcode'] not in skip_arxiv]
    else:
        identifiers = [b for b in ids]
    return identifiers

def get_thumbnails(d):
    query = {
        u'page': 1,
        u'show': 200,
        u'publicationID': unicode(d)
        }
    base_url = 'http://www.astroexplorer.org'
    new_id = base64.b64encode(json.dumps(query)).decode('utf8').rstrip('=')
    query_url = "{0}/search/{1}".format(base_url, new_id)

    response = requests.get(query_url)

    soup = BeautifulSoup(response.text, "html.parser")
    thumbs = []
    for tag in soup.find_all('div', {'class':"thumbnail-container"}):
        highres =  "{0}{1}".format(base_url, tag.a['href'])
        thumb = tag.img['src']
        thumbs.append((thumb, highres))

    return thumbs

def process_IOP_graphics(identifiers, force, dryrun=False):
    """
    For the set of identifiers supplied, retrieve the graphics data.
    If force is false, skip a bibcode if already in the database. The list of
    identifiers is a list of dictionaries because for all records we need the
    bibcode (to check if a record already exists) and the arXiv ID, to find
    the full text TAR archive
    :param bibcodes:
    :param force:
    :return:
    """
    # Regular expression for parsing full text files
    doi_pat = re.compile(
        '''<article-id\s+pub-id-type="doi">(?P<doi>.*?)</article-id>''')
    print_issn_pat = re.compile(
		    '<issn\ pub-type="ppub">(?P<issn>.{4}-.{4})</issn>', re.VERBOSE | re.DOTALL | re.IGNORECASE)
    artid_pat = re.compile('<elocation-id\ content-type="artnum">(?P<artid>\d+)</elocation-id>')
    volume_pat = re.compile('<volume>(?P<volume>\d+)</volume>')
    issue_pat  = re.compile('<issue>(?P<issue>\d+)</issue>')
    # Create the mapping from bibcode to full text location
    bibcode2fulltext = {}
    map_file = current_app.config.get('GRAPHICS_FULLTEXT_MAPS').get('IOP')
    with open(map_file) as fh_map:
        for line in fh_map:
            try:
                bibcode, ft_file, source = line.strip().split('\t')
                if ft_file[-3:].lower() == 'xml':
                    bibcode2fulltext[bibcode] = ft_file
            except:
                continue
    # Get translations from DOI to file path
    doi2path = {}
    trans_file = current_app.config.get('GRAPHICS_FULLTEXT_TRANSLATION').get('IOP')
    with open(trans_file) as fh_trans:
        for line in fh_trans:
            try:
                b, d, ft, t = line.strip().split('\t')
                doi2path[d] = os.path.dirname(ft).replace('/proj/ads/articles/sources/STACKS/','').strip()
            except:
                continue
    # If there is back data for image data, load this
    back_file = current_app.config.get('GRAPHICS_BACK_DATA_FILE').get('IOP')
    id2thumb = {}
    if back_file and os.path.exists(back_file):
        with open(back_file) as back_data:
            for line in back_data:
                doi, id, thumb = line.strip().split(',')
                id2thumb[doi] = thumb
    # Get source name
    src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('IOP')
    # Now process the records submitted
    nfigs = None
    updates = []
    new = []
#    bibcodes = [b['bibcode'] for b in identifiers]
    for entry in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)
        else:
            continue
    # First process the updates
    for paper in updates:
        # Get the full text for this article
        ft_file = bibcode2fulltext.get(paper['bibcode'], None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            # No full text file, skip
            continue
#        dmat = doi_pat.search(buffer)
#        try:
#            DOI = dmat.group('doi')
#        except:
#            sys.stderr.write('Cannot find DOI: %s\n' % ft_file)
#            continue
        # Establish the article path used in Astroexplorer URLs
        art_path = doi2path.get(paper['doi'], "NA")
        # Get the print ISSN number
        pmat = print_issn_pat.search(buffer)
        try:
            print_issn = pmat.group('issn')
        except:
            print_issn = None
        # Get volume and issue numbers
        vmat = volume_pat.search(buffer)
        try:
            volno = vmat.group('volume')
        except:
            volno = None
        imat = issue_pat.search(buffer)
        try:
            issno = imat.group('issue')
        except:
            issno = None
        # Get the article ID
        imat = artid_pat.search(buffer)
        try:
            artid = imat.group('artid')
        except:
            artid = None
        nfigs = manage_IOP_graphics(buffer, paper['bibcode'], paper['doi'], art_path, src, id2thumb,
                                    update=True, dryrun=dryrun)

    # Next, process the new records
    for paper in new:
        # Get the full text for this article
        ft_file = bibcode2fulltext.get(paper['bibcode'], None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            # No full text file, skip
            if ft_file:
                sys.stderr.write('Incorrect full text mapping for %s: %s\n'%(paper, ft_file))
            else:
                sys.stderr.write('No full text found for %s\n' % paper)
            continue
#        dmat = doi_pat.search(buffer)
#        try:
#            DOI = dmat.group('doi')
#        except:
#            sys.stderr.write('Cannot find DOI: %s\n' % ft_file)
#            continue
        # Establish the article path used in Astroexplorer URLs
        art_path = doi2path.get(paper['doi'], "NA")
        # Get the print ISSN number
        pmat = print_issn_pat.search(buffer)
        try:
            print_issn = pmat.group('issn')
        except:
            print_issn = None
        # Get volume and issue numbers
        vmat = volume_pat.search(buffer)
        try:
            volno = vmat.group('volume')
        except:
            volno = None
        imat = issue_pat.search(buffer)
        try:
            issno = imat.group('issue')
        except:
            issno = None
        # Get the article ID
        imat = artid_pat.search(buffer)
        try:
            artid = imat.group('artid')
        except:
            artid = None
        try:
            nfigs = manage_IOP_graphics(buffer, paper['bibcode'], paper['doi'], art_path, src, id2thumb, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing %s (%s)\n'%(paper, e))
            continue
    return nfigs

def manage_IOP_graphics(fulltext, bibcode, DOI, apath, source, id2thumb,
                        update=False, dryrun=False):
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == bibcode).first()
    else:
        graphic = None
    # URL templates for thumbnail images and high res images
    thumbURL = "https://s3.amazonaws.com/aasie/images/%s/%s_tb.%s"
    loresURL = "https://s3.amazonaws.com/aasie/images/%s/%s_lr.%s"
    highURL = "http://www.astroexplorer.org/details/%s"
    # IOP / non-AAS journal: use IOPscience URLs
    IOPscienceURL_lr = "http://cdn.iopscience.com/images/%s/Full/%s_%s.%s"
    IOPscienceURL_hr = "http://cdn.iopscience.com/images/%s/Full/%s_hr.%s"
    # Regular expression for parsing full text files
    fig_pat = re.compile(
        r'''<fig(-section)?\s+id="(?P<figID>.*?)"(?P<rest>.*?)>(?P<figure>.*?)
        </fig(-section)?>''', re.VERBOSE | re.DOTALL | re.IGNORECASE)
    lbl_pat = re.compile(
        r'''<label>(?P<label>.*?)</label>''',
        re.VERBOSE | re.DOTALL | re.IGNORECASE)
    cap_pat = re.compile(
        r'''<caption.*?>(?P<caption>.*?)</caption>''',
        re.VERBOSE | re.DOTALL | re.IGNORECASE)
    thumb_pat = re.compile(
        '<graphic\s+id="(?P<id>.*?)"\s+'
        'content-type="thumb"\s+'
        'alt-version="yes"\s+xlink:href="(?P<href>.*?)"/>')
    # <graphic mimetype="image" position="float" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:type="simple" xlink:href="fg7.jpg" content-type="online"/>
    lores_pat = re.compile(
        '<graphic\s+id="(?P<id>.*?)"\s+'
        'content-type="(?P<ctype>low|online)"\s+'
        'xlink:href="(?P<href>.*?)"/>')
    online_pat = re.compile(
        '<graphic\s+id="(?P<id>.*?)"\s+'
        'content-type="online"\s+'
        'xlink:href="(?P<href>.*?)"/>')
    online_alt_pat = re.compile(
        '<graphic\s+mimetype="image"\s+position="float"\ xmlns:xlink="http://www.w3.org/1999/xlink"\ xlink:type="simple"\ xlink:href="(?P<href>.*?)"\ content-type="(?P<ctype>low|online)"/>')
    seen = []
    done = []
    figures = []
    try:
        thumbnails = get_thumbnails(DOI)
    except:
        thumbnails = []
#    # Strip publisher part from DOI to use in thumbnail URL
#    if bibcode[4:9] in AAS or bibcode[4:9] in IOPscience:
#        if apath:
#            art_path = apath
#        else:
#            art_path = DOI
#    else:
#        art_path = re.sub('^.*?/', '', DOI)
#    # Retrieve information for all figures
#    cursor = 0
#    amat = fig_pat.search(fulltext, cursor)
#    while amat:
#        fig_data = {}
#        images = []
#        id = amat.group('figID')
#        fg = amat.group('figure')
#        figtype = 'regular'
#        try:
#            rest = amat.group('rest')
#            if rest.find('fig-type="interactive"') > -1:
#                figtype = 'interactive'
#        except:
#            pass
#        lm = lbl_pat.search(fg)
#        try:
#            label = lm.group('label')
#        except:
#            label = 'Figure'
#        cm = cap_pat.search(fg)
#        try:
#            caption = cm.group('caption')
#            caption = re.sub('</?(xref|p).*?>', '', caption)
#        except:
#            caption = None
#        fig_data['figure_id'] = id
#        if label:
#            fig_data['figure_label'] = label.replace('&#x02003;','')
#        else:
#            fig_data['figure_label'] = ''
#        fig_data['figure_caption'] = caption
#        fig_data['figure_type'] = figtype
#        cs = 0
#        imat = thumb_pat.search(fg, cs)
#        while imat:
#            try:
#                image_id = imat.group('id').split('_')[0]
#            except:
#                image_id = imat.group('href').split('.')[0]
#            format = 'gif'
#            if imat.group('href').split('.')[-1].lower() == 'jpg':
#                format = 'jpg'
#            thumb = id2thumb.get(
#                image_id, thumbURL % (art_path, image_id, format))
#            if bibcode[4:9] not in AAS:
#                thumb = IOPscienceURL_lr % (art_path, image_id, "lr", format)
#            # fix for AJ (they use print ISSN in file path, but electronic ISSN
#            # in URL
#            if bibcode[4:9] == 'AJ...':
#                thumb = thumb.replace('0004-6256', '1538-3881')
#            # Unfortunately we have to test if the thumbnail URL exists
#            check = requests.get(thumb)
#            if image_id not in done:
#                if int(check.status_code) == 200:
#                    if bibcode[4:9] in AAS:
#                        highres = highURL % image_id
#                    else:
#                        highres = "http://dx.doi.org/%s" % DOI
#                    if thumb not in seen:
#                        images.append({'image_id': image_id,
#                               'format': format,
#                               'thumbnail': thumb,
#                               'highres': highres})
#                        done.append(image_id)
#                        seen.append(thumb)
#                else:
#                    sys.stderr.write('Thumb URL returned status %s: %s (%s)\n'%(check.status_code, thumb, bibcode))
#            cs = imat.end()
#            imat = thumb_pat.search(fg, cs)
#        # The images list will be empty for articles mid-2015 on, since it
#        # seems that there is no longer a thumbnail entry among the graphics
#        # entries. The low-res seems to have taken it place. Therefore we
#        # need to parse out those in that case (or where the type is "online")
#        if len(images) == 0:
#            cs = 0
#            if bibcode[4:9] != 'PASP.':
#                search_pat = lores_pat
#                alt = False
#            else:
#                search_pat = online_alt_pat
#                alt = True
#            imat = search_pat.search(fg, cs)
#            while imat:
#                try:
#                    image_id = imat.group('id').split('_')[0]
#                except:
#		                image_id = imat.group('href').split('.')[0]
#                if imat.group('ctype') == 'low':
#                    ctype = 'lr'
#                else:
#                    ctype = 'online'
#                format = 'gif'
#                if imat.group('href').split('.')[-1].lower() == 'jpg':
#                    format = 'jpg'
#                thumb = id2thumb.get(
#                    image_id, thumbURL % (art_path, image_id, format))
#                if bibcode[4:9] not in AAS:
#                    thumb = IOPscienceURL_lr % (art_path, image_id, ctype, format)
#                if alt:
#                    thumb = thumb.replace('_online','')
#                # fix for AJ (they use print ISSN in file path, but electronic ISSN
#                # in URL
#                if bibcode[4:9] == 'AJ...':
#                    thumb = thumb.replace('0004-6256', '1538-3881')
#                # Unfortunately we have to test if the thumbnail URL exists
#                check = requests.get(thumb)
#                if image_id not in done:
#                    if int(check.status_code) == 200:
#                        if bibcode[4:9] in AAS:
#                            highres = highURL % image_id
#                        else:
#                            highres = "http://dx.doi.org/%s" % DOI
#                        if thumb not in seen:
#                            images.append({'image_id': image_id,
#                                   'format': format,
#                                   'thumbnail': thumb,
#                                   'highres': highres})
#                            done.append(image_id)
#                            seen.append(thumb)
#                    else:
#                        sys.stderr.write('Thumb URL returned status %s: %s (%s)\n'%(check.status_code, thumb, bibcode))
#                cs = imat.end()
#                imat = search_pat.search(fg, cs)
#        if len(images) > 0:
#            fig_data['images'] = images
#            figures.append(fig_data)
#        cursor = amat.end()
#        amat = fig_pat.search(fulltext, cursor)
    if len(thumbnails) > 0 and not dryrun:
        graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('IOP')
        if update:
            sys.stderr.write('Updating %s\n'%bibcode)
            graphic.source = graph_src
            graphic.figures = []
            graphic.thumbnails = thumbnails
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%bibcode)
            graphic = GraphicsModel(
                bibcode=bibcode,
                doi=DOI,
                source=graph_src,
                eprint=False,
                figures=[],
                thumbnails=thumbnails,
                modtime=datetime.now()
            )
            db.session.add(graphic)
        db.session.commit()
    if not dryrun:
        return len(thumbnails)
    else:
        return thumbnails


def process_arXiv_graphics(identifiers, force, dryrun=False):
    """
    For the set of bibcodes supplied, retrieve the graphics data.
    If force is false, skip a bibcode if already in the database.
    :param identifiers:
    :param force:
    :return:
    """
    updates = []
    new = []
    ft_base = current_app.config.get('GRAPHICS_FULLTEXT_MAPS').get('arXiv')
    # Process the identifiers submitted
    for identifier in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == identifier['bibcode']).first()
        if force and resp:
            updates.append(identifier)
        elif not resp:
            new.append(identifier)
        else:
            continue
    # First process the updates
    yy = '9999'
    aid= '9999'
    cat= 'arXiv'
    for entry in updates:
        paper = entry['arxid']
        bibcode = entry['bibcode']
        if '/' in paper:
            cat = paper.split('/')[0]
            year = paper.split('/')[1][:2]
            if int(year) > 80:
                yy = "19%s" % year
            else:
                yy = "20%s" % year
            aid = paper.split('/')[1]
        elif ':' in paper:
            cat = 'arXiv'
            yy = paper.split(':')[1].split('.')[0]
            aid = paper.split(':')[1].split('.')[1]
        ft_file = "%s/%s/%s/%s.tar.gz" % (ft_base, cat, yy, aid)
        if not os.path.exists(ft_file):
            continue
        # We found a TAR archive to be processed
        res = manage_arXiv_graphics(ft_file, bibcode, paper, cat, dryrun=dryrun, update=True)

    for entry in new:
        paper = entry['arxid']
        bibcode = entry['bibcode']
        if '/' in paper:
            cat = paper.split('/')[0]
            year = paper.split('/')[1][:2]
            if int(year) > 80:
                yy = "19%s" % year
            else:
                yy = "20%s" % year
            aid = paper.split('/')[1]
        elif ':' in paper:
            cat = 'arXiv'
            yy = paper.split(':')[1].split('.')[0]
            aid = paper.split(':')[1].split('.')[1]
        ft_file = "%s/%s/%s/%s.tar.gz" % (ft_base, cat, yy, aid)
        if not os.path.exists(ft_file):
            continue
        # We found a TAR archive to be processed
        res = manage_arXiv_graphics(ft_file, bibcode, paper, cat, dryrun=dryrun)
    return res

def manage_arXiv_graphics(ft_file, bibcode, arx_id, category, update=False, dryrun=False):
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
                GraphicsModel.bibcode == bibcode).first()
        if not graphic:
            sys.stderr.write('Note: update for %s, but no existing record found!\n'%bibcode)
    else:
        graphic = None
    # First get lists of (La)TeX and image files
    tex_files, img_files, xdir = file_ops.untar(ft_file, bibcode)
    # If we didn't find any image files, skip
    if len(img_files) == 0:
        return
    figures = []
    # Next convert the image files
    # All the original images than cannot be converted will be
    # removed from the list of originals
    try:
        img_files, converted_images = file_ops.convert_images(img_files)
    except Exception, exc:
        sys.stderr.write('Image conversion barfed for %s. Skipping.\n'%bibcode)
        # Remove the temporary directory
        try:
            shutil.rmtree(xdir)
        except:
            pass
        return
    # We now have a list with successfully converted (PNG) images
    extracted_image_data = []
    for tex_file in tex_files:
        # Extract images, captions and labels
        partly_extracted_image_data = extract_captions(tex_file, xdir,
                                                       img_files)
        if not partly_extracted_image_data == []:
            # Add proper filepaths and do various cleaning
            cleaned_image_data = prepare_image_data(partly_extracted_image_data,
                                                    tex_file, converted_images)

            # Using prev. extracted info, get contexts for each image found
            extracted_image_data.extend((extract_context(tex_file,
                                                         cleaned_image_data)))
    extracted_image_data = remove_dups(extracted_image_data)
    # For those images whereno metadata was captured, keep them with
    # empty strings
    try:
        skipped_images = [i for i in converted_images if i not in [e[0] for e in extracted_image_data]]
    except:
        skipped_images = converted_images
    if len(skipped_images) > 0:
        extracted_image_data += [(im,'','',[]) for im in skipped_images]
    fid = 1
    source2target = {}
    source2AWS = {}
    for item in extracted_image_data:
        if not os.path.exists(item[0]) or not item[0].strip():
            continue
        fig_data = {}
        if arx_id.find('arXiv') > -1:
            figure_id = 'arxiv%s_f%s' % (arx_id.replace('arXiv:', ''), fid)
            subdir = arx_id.replace('arXiv:', '').split('.')[0]
            eprdir = arx_id.replace('arXiv:', '').split('.')[1]
        else:
            figure_id = '%s_f%s' % (arx_id.replace('/', '_'), fid)
            subdir = arx_id.split('/')[1][:4]
            eprdir = arx_id.split('/')[1][4:]
        source2target[item[0]] = "%s/%s/%s/%s/%s.png" % (
            current_app.config.get('GRAPHICS_IMAGE_DIR'),
            category,
            subdir,
            eprdir,
            figure_id)
        source2AWS[item[0]] = "seri/arXiv/%s/%s/%s/%s.png" % (
            category,
            subdir,
            eprdir,
            figure_id)
        fig_data['figure_id'] = figure_id
        try:
            fig_data['figure_label'] = item[2].encode('ascii','ignore')
        except:
            fig_data['figure_label'] = '' 
        if not fig_data['figure_label']:
            fig_data['figure_label'] = 'figure %s' % fid
        try:
            fig_data['figure_caption'] = item[1].encode('ascii','ignore')
        except:
            fig_data['figure_caption'] = ''
        image_url = "http://arxiv.org/abs/%s" % arx_id.replace('arXiv:','')
        thumb_url = "%s/%s/%s" % (
            current_app.config.get('GRAPHICS_AWS_S3_URL'),
            current_app.config.get('GRAPHICS_AWS_S3_BUCKET'),
            source2AWS[item[0]])
        fig_data['images'] = [
            {
                'image_id': fid,
                'format': 'png',
                'thumbnail': thumb_url,
                'highres': image_url
            }
        ]
        figures.append(fig_data)
        fid += 1
    # Now it is time to move the PNGs to their final location, renaming
    # them in the process
    # 1. Store them on a local server
    # 2. Store them on AWS S3
    # Create the S3 session and copy over the files
    client = get_boto_session().client('s3')
    # Currently we just process PNG files
    mimetype = 'image/png'
    bucket = current_app.config.get('GRAPHICS_AWS_S3_BUCKET')
    for source, target in source2target.items():
        # Copy image file from TMP location to final location on disk
        target_dir, fname = os.path.split(target)
        if not os.path.exists(target_dir):
            cmmd = 'mkdir -p %s' % target_dir
            commands.getoutput(cmmd)
        shutil.copy(source, target)
        # Upload image file to S3
        key = source2AWS[source]
        try:
            data = open(source, 'rb')
        except Exception, e:
            sys.stderr.write('Error loading image data for %s: %s\n' % (source, str(e)))
            continue
        client.put_object(Key=key, Bucket=bucket ,Body=data, ACL='public-read', ContentType=mimetype)
    # Now it's time to clean up stuff we've extracted
    TMP_DIR = current_app.config.get('GRAPHICS_TMP_DIR')
    extract_dir = "%s/%s" % (TMP_DIR, bibcode)
    try:
        shutil.rmtree(extract_dir)
    except:
        pass
    # Finally update the database
    graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('arXiv')
    if len(figures) > 0 and not dryrun:
        if update:
            sys.stderr.write('Updating %s\n'%bibcode)
            graphic.source = graph_src
            graphic.figures = figures
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%bibcode)
            try:
                graphic = GraphicsModel(
                    bibcode=bibcode,
                    doi=arx_id,
                    source=graph_src,
                    eprint=True,
                    figures=figures,
                    modtime=datetime.now()
                )
                db.session.add(graphic)
            except Exception, e:
                sys.stderr.write('Failed adding data for %s: %s\n'%(bibcode, e))
        try:
            db.session.commit()
        except Exception, e:
            sys.stderr.write('Data commit failed for %s: %s\n'%(bibcode, e))

    if not dryrun:
        return len(figures)
    else:
        return figures

def process_Elsevier_graphics(identifiers, force, dryrun=False):
    """
    For the set of identifiers supplied, retrieve the graphics data.
    If force is false, skip a bibcode if already in the database. The list of
    identifiers is a list of dictionaries because for all records we need the
    bibcode (to check if a record already exists) and the arXiv ID, to find
    the full text TAR archive
    :param bibcodes:
    :param force:
    :return:
    """
    # Process the records submitted
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)
        else:
            continue
    # First process the updates
    for paper in updates:
        nfigs = manage_Elsevier_graphics(paper, update=True, dryrun=dryrun)
    # Next, process the new records
    for paper in new:
        try:
            nfigs = manage_Elsevier_graphics(paper, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing %s (%s)\n'%(paper, e))
            continue
    return nfigs

def manage_Elsevier_graphics(record, update=False, dryrun=False):
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None
    # URL templates for thumbnail images
    thumbURL = "https://ars.els-cdn.com/content/image/%s"
    queryURL = "https://api.elsevier.com/content/object/doi/%s"
    figures = []
    # Retrieve graphics info from Elsevier API
    APIkey = current_app.config.get('ELSEVIER_API_KEY')
    headers = {
        'Accept': 'application/json',
        'X-ELS-APIKey': APIkey
    }
    payload = {'view':'META','field':'url,identifier,description'}
    r = requests.get(queryURL % record.get('doi'), params=payload, headers=headers)
    try:
        PII = r.json()['attachment-metadata-response']['coredata']['dc:identifier'].replace('PII:','')
    except:
        PII = None
    # Retrieve information for all figures
    try:
        thumbs = [r for r in r.json()['attachment-metadata-response']['attachment'] if r['type'] == 'IMAGE-THUMBNAIL']
    except:
        thumbs = []
    for thumb in thumbs:
        fig_data = {}
        images = []
        try:
            fignr = int(re.sub("[^0-9]", "",thumb['ref']))
        except:
            fignr = re.sub("[^0-9]", "",thumb['ref'])
        fig_data['figure_id'] = thumb['eid']
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        if PII:
            highres = "https://www.sciencedirect.com/science/article/pii/%s" % PII
        else:
            highres = "https://dx.doi.org/%s" % record['doi']
        image = {'image_id': thumb['eid'], 
                 'thumbnail': thumbURL % thumb['eid'],
                 'format': thumb['mimetype'].split('/')[1],
                 'highres': highres}
        fig_data['images'] = [image]
        figures.append(fig_data)
    figures = sorted(figures, key=itemgetter('figure_number'))
    if len(figures) > 0 and not dryrun:
        graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('Elsevier')
        if update:
            sys.stderr.write('Updating %s\n'%record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                modtime=datetime.now()
            )
            db.session.add(graphic)
        db.session.commit()
    if not dryrun:
        return len(figures)
    else:
        return figures

def process_EDP_graphics(identifiers, force, dryrun=False):
    """
    For the set of identifiers supplied, retrieve the graphics data.
    If force is false, skip a bibcode if already in the database. The list of
    identifiers is a list of dictionaries because for all records we need the
    bibcode (to check if a record already exists) and the arXiv ID, to find
    the full text TAR archive
    :param bibcodes:
    :param force:
    :return:
    """
    # Create the mapping from bibcode to full text location
    bibcode2fulltext = {}
    map_file = current_app.config.get('GRAPHICS_FULLTEXT_MAPS').get('EDP')
    with open(map_file) as fh_map:
        for line in fh_map:
            try:
                bibcode, ft_file, source = line.strip().split('\t')
                if ft_file[-3:].lower() == 'xml':
                    bibcode2fulltext[bibcode] = ft_file
            except:
                continue
    # Get source name
    src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('EDP')
    # Now process the records submitted
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)
        else:
            continue
    # First process the updates
    nfigs = None
    for paper in updates:
        # Get the full text for this article
        fulltext = bibcode2fulltext.get(paper['bibcode'], None)
        if not fulltext:
            # No full text file, skip
            sys.stderr.write('No full text found for %s (update)\n' % paper['bibcode'])
            continue
        try:
             nfigs = manage_EDP_graphics(paper, fulltext, update=True, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing update %s (%s)\n'%(paper['bibcocde'], e))
            continue
    # Next, process the new records
    for paper in new:
        # Get the full text for this article
        fulltext = bibcode2fulltext.get(paper['bibcode'], None)
        if not fulltext:
            # No full text file, skip
            sys.stderr.write('No full text found for %s (new record)\n' % paper['bibcode'])
            continue
        try:
            nfigs = manage_EDP_graphics(paper, fulltext, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing new %s (%s)\n'%(paper['bibcode'], e))
            continue
    return nfigs

def manage_EDP_graphics(record, ft_file, update=False, dryrun=False):
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None
    # Get the article identifier from the full text file name
    identifier = os.path.basename(ft_file).replace('.xml','')
    # and get the location of the full text files
    srcdir = current_app.config.get('GRAPHICS_GRAPHICS_LOCATION').get('EDP')
    # Get the JPEG files in the source directory
    thumbs = glob.glob('%s/%s/*.jpg'%(srcdir, identifier))
    # Filter out any images with 'small' in the file name
    # and that don't have 'fig' in the file name  
    thumbs = [t for t in thumbs if t.lower().find('fig') > -1 and t.lower().find('small') == -1]
    #thumbs = [t for t in thumbs if t.lower().find('small') > -1]
    # On S3, thumbnails go to
    #  <bucket>/seri/A+A/<volume>/<article ID>
    bucket = current_app.config.get('GRAPHICS_AWS_S3_BUCKET')
    volno = record['bibcode'][9:13].replace('.','0')
    thumb_bucket = "seri/A+A/%s/%s" % (volno, identifier)
    # Create the S3 session and copy over the files
    client = get_boto_session().client('s3')
    # Currently we just process JPEG files
    mimetype = 'image/jpeg'
    # Copy files over to S3
    figures = []
    for thumb in thumbs:
        fig_data = {}
        images = []
        # Try to distill the figure number from file name
        try:
            fignr = int(re.sub('^.*fig(\d+).*',r'\1',os.path.basename(thumb)))
        except:
            fignr = 0
        fig_data['figure_id'] = re.sub('^(.*)\..*',r'\1',os.path.basename(thumb))
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        highres = "http://dx.doi.org/%s" % record['doi']
        # S3 URL for thumbnail is:
        # https://s3.amazonaws.com/adsabs-thumbnails/seri/A%2BA/0595/aa29175-16/aa29175-16-fig1.jpg
        key = "%s/%s" % (thumb_bucket, os.path.basename(thumb))
        thumbURL = "%s/%s/%s" % (current_app.config.get('GRAPHICS_AWS_S3_URL'), bucket, urllib.quote(key))
        image = {'image_id': re.sub('^(.*)\..*',r'\1',os.path.basename(thumb)),
                 'thumbnail': thumbURL,
                 'format': mimetype.split('/')[1],
                 'highres': highres}
        fig_data['images'] = [image]
        figures.append(fig_data)
        # Upload the image to S3
        try:
            data = open(thumb, 'rb')
        except Exception, e:
            sys.stderr.write('Error loading image data for %s: %s\n' % (thumb, str(e)))
            continue
        client.put_object(Key=key, Bucket=bucket ,Body=data, ACL='public-read', ContentType=mimetype)
    figures = sorted(figures, key=itemgetter('figure_number'))
    if len(figures) > 0 and not dryrun:
        graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('EDP')
        if update:
            sys.stderr.write('Updating %s\n'%record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                modtime=datetime.now()
            )
            db.session.add(graphic)
        db.session.commit()

def process_OUP_graphics(identifiers, force, dryrun=False):
    """
    For the set of identifiers supplied, retrieve the graphics data.
    If force is false, skip a bibcode if already in the database. The list of
    identifiers is a list of dictionaries because for all records we need the
    bibcode (to check if a record already exists) and the arXiv ID, to find
    the full text TAR archive
    :param bibcodes:
    :param force:
    :return:
    """
    # Get source name
    src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('OUP')
    # Now process the records submitted
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)
        else:
            continue
    # First process the updates
    nfigs = None
    for paper in updates:
        # Get the full text for this article
        try:
             nfigs = manage_OUP_graphics(paper, update=True, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing update %s (%s)\n'%(paper['bibcocde'], e))
            continue
    # Next, process the new records
    for paper in new:
        try:
            nfigs = manage_OUP_graphics(paper, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing new %s (%s)\n'%(paper['bibcode'], e))
            continue
    return nfigs

def manage_OUP_graphics(record, update=False, dryrun=False):
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None
    # Get identifier from DOI
    identifier = record['doi'].split('/')[-1]
    volume     = record['bibcode'][9:13].replace('.','').strip()
    # and get the location of the full text files
    srcdir = current_app.config.get('GRAPHICS_GRAPHICS_LOCATION').get('OUP')
    # Get the JPEG files in the source directory
    thumbs = glob.glob('%s/%s/%s/*.jpeg'%(srcdir, volume, identifier))
    # On S3, thumbnails go to
    #  <bucket>/seri/MNRAS/<volume>/<article ID>
    bucket = current_app.config.get('GRAPHICS_AWS_S3_BUCKET')
    volno = record['bibcode'][9:13].replace('.','0')
    thumb_bucket = "seri/MNRAS/%s/%s" % (volno, identifier)
    # Create the S3 session and copy over the files
    client = get_boto_session().client('s3')
    # Currently we just process JPEG files
    mimetype = 'image/jpeg'
    # Copy files over to S3
    figures = []
    for thumb in thumbs:
        fig_data = {}
        images = []
        # Try to distill the figure number from file name
        try:
            fignr = os.path.basename(thumb).replace(identifier,'').replace('fig','').replace('.jpeg','').strip()
        except:
            fignr = "0"
        fig_data['figure_id'] = re.sub('^(.*)\..*',r'\1',os.path.basename(thumb))
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        highres = "http://dx.doi.org/%s" % record['doi']
        # S3 URL for thumbnail is:
        # https://s3.amazonaws.com/adsabs-thumbnails/seri/A%2BA/0595/aa29175-16/aa29175-16-fig1.jpg
        key = "%s/%s" % (thumb_bucket, os.path.basename(thumb))
        thumbURL = "%s/%s/%s" % (current_app.config.get('GRAPHICS_AWS_S3_URL'), bucket, urllib.quote(key))
        image = {'image_id': re.sub('^(.*)\..*',r'\1',os.path.basename(thumb)),
                 'thumbnail': thumbURL,
                 'format': mimetype.split('/')[1],
                 'highres': highres}
        fig_data['images'] = [image]
        figures.append(fig_data)
        # Upload the image to S3
        try:
            data = open(thumb, 'rb')
        except Exception, e:
            sys.stderr.write('Error loading image data for %s: %s\n' % (thumb, str(e)))
            continue
        client.put_object(Key=key, Bucket=bucket ,Body=data, ACL='public-read', ContentType=mimetype)
    figures = sorted(figures, key=itemgetter('figure_number'))
    if len(figures) > 0 and not dryrun:
        graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('OUP')
        if update:
            sys.stderr.write('Updating %s\n'%record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                modtime=datetime.now()
            )
            db.session.add(graphic)
        db.session.commit()

def process_APS_graphics(identifiers, force, dryrun=False):
    figure_pat = re.compile('<fig\ (?P<figno>.*?)>')
    # Get source name
    src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('APS')
    # Get mapping to fulltext files
    bibcode2fulltext = {}
    map_file = current_app.config.get('GRAPHICS_FULLTEXT_MAPS').get('APS')
    with open(map_file) as fh_map:
        for line in fh_map:
            bibcode, ft_file, source = line.strip().split('\t')
            if ft_file.endswith('.xml'):
                bibcode2fulltext[bibcode] = ft_file
    # Now process the records submitted
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)
        else:
            continue
    # First process the updates
    nfigs = None
    for paper in updates:
        # Get the full text for this article
        ft_file = bibcode2fulltext.get(paper, None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            # No full text file, skip
            continue
        # Get the figure count
        match = figure_pat.findall(buffer)
        figure_num = len(match)
        # Get the full text for this article
        try:
             nfigs = manage_APS_graphics(paper, figure_num, update=True, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing update %s (%s)\n'%(paper['bibcocde'], e))
            continue
    # Next, process the new records
    for paper in new:
        # Get the full text for this article
        ft_file = bibcode2fulltext.get(paper['bibcode'], None)
        if ft_file and os.path.exists(ft_file):
            buffer = open(ft_file).read()
        else:
            # No full text file, skip
            continue
        # Get the figure count
        match = figure_pat.findall(buffer)
        figure_num = len(match)
        try:
            nfigs = manage_APS_graphics(paper, figure_num, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing new %s (%s)\n'%(paper['bibcode'], e))
            continue
    return nfigs

def manage_APS_graphics(record, num_figs, update=False, dryrun=False):
    # https://journals.aps.org/prd/article/10.1103/PhysRevD.95.043541/figures/1/small
    # Thumbnail URL takes DOI as first argument and figure number as second
    thumbURL = "https://journals.aps.org/prd/article/%s/figures/%s/small"
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None
    
    figures = []
    for i in range(1, num_figs+1):
        fig_data = {}
        images = []
        
        fig_data['figure_id'] = "Figure %s" % i
        fig_data['figure_label'] = "Figure %s" % i
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = i
        highres = "http://dx.doi.org/%s" % record['doi']
        image = {'image_id': i,
                 'thumbnail': thumbURL % (record['doi'], i),
                 'format': 'jpeg',
                 'highres': highres}
        fig_data['images'] = [image]
        figures.append(fig_data)
    
    figures = sorted(figures, key=itemgetter('figure_number'))
    if len(figures) > 0 and not dryrun:
        graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('APS')
        if update:
            sys.stderr.write('Updating %s\n'%record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                modtime=datetime.now()
            )
            db.session.add(graphic)
        db.session.commit()
    
def process_AnnRev_graphics(identifiers, force, dryrun=False):

    # Get source name
    src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('AnnRev')
    # Now process the records submitted
    nfigs = None
    updates = []
    new = []
    for entry in identifiers:
        resp = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == entry['bibcode']).first()
        if force and resp:
            updates.append(entry)
        elif not resp:
            new.append(entry)
        else:
            continue
    # First process the updates
    for paper in updates:
        # Get the full text for this article
        try:
             nfigs = manage_AnnRev_graphics(paper, update=True, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing update %s (%s)\n'%(paper['bibcocde'], e))
            continue
    # Next, process the new records
    for paper in new:
        try:
            nfigs = manage_AnnRev_graphics(paper, dryrun=dryrun)
        except Exception, e:
            sys.stderr.write('Error processing new %s (%s)\n'%(paper['bibcode'], e))
            continue
    return nfigs

def manage_AnnRev_graphics(record, update=False, dryrun=False):
    # If we're updating, grab the existing database entry
    if update:
        graphic = db.session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == record['bibcode']).first()
    else:
        graphic = None
    # and get the location of harvested graphics info
    srcdir = current_app.config.get('GRAPHICS_GRAPHICS_LOCATION').get('AnnRev')
    # Get the JPEG files in the source directory
    graph_file = '%s/images/%s.json'%(srcdir, record['bibcode'].replace('&','+'))
    try:
        thumbs = json.loads(open(graph_file).read())['images']
    except:
        thumbs = []
    mimetype = 'image/gif'
    # Copy files over to S3
    figures = []
    for thumb in thumbs:
        fig_data = {}
        images = []
        # Try to distill the figure number from file name
        try:
            fignr = os.path.basename(thumb).split('.')[1].replace('f','').strip()
        except:
            fignr = "0"
        fig_data['figure_id'] = re.sub('^(.*)\..*',r'\1',os.path.basename(thumb)).replace('.gif','')
        fig_data['figure_label'] = "Figure %s" % fignr
        fig_data['figure_caption'] = ''
        fig_data['figure_number'] = fignr
        highres = "http://dx.doi.org/%s" % record['doi']
        thumbURL = thumb
        image = {'image_id': re.sub('^(.*)\..*',r'\1',os.path.basename(thumb)),
                 'thumbnail': thumbURL,
                 'format': mimetype.split('/')[1],
                 'highres': highres}
        fig_data['images'] = [image]
        figures.append(fig_data)
    figures = sorted(figures, key=itemgetter('figure_number'))
    if len(figures) > 0 and not dryrun:
        graph_src = current_app.config.get('GRAPHICS_SOURCE_NAMES').get('AnnRev')
        if update:
            sys.stderr.write('Updating %s\n'%record['bibcode'])
            graphic.source = graph_src
            graphic.figures = figures
            graphic.modtime = datetime.now()
        else:
            sys.stderr.write('Creating new record for %s\n'%record['bibcode'])
            graphic = GraphicsModel(
                bibcode=record['bibcode'],
                doi=record['doi'],
                source=graph_src,
                eprint=False,
                figures=figures,
                modtime=datetime.now()
            )
            db.session.add(graphic)
        db.session.commit()
