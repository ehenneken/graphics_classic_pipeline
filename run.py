#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import argparse
import importlib
from collections import defaultdict
from datetime import datetime

# Add root (for config, local_config) and service/ (for all other modules)
_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_root, 'graphics'))
sys.path.insert(0, _root)

from models import GraphicsModel, AlchemyEncoder, get_session
import tasks


def load_config():
    """Load config.py then overlay local_config.py if present."""
    conf = {}
    base = importlib.import_module('config')
    conf.update({k: v for k, v in vars(base).items() if not k.startswith('_')})
    try:
        local = importlib.import_module('local_config')
        conf.update({k: v for k, v in vars(local).items() if not k.startswith('_')})
    except ImportError:
        pass
    return conf


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_checkdb(session, config, args):
    if args.identifier:
        record = session.query(GraphicsModel).filter(
            GraphicsModel.bibcode == args.identifier).first()
        if not record:
            sys.exit('No record found for %s\n' % args.identifier)
        print(record.figures)
    elif args.set:
        query = session.query(GraphicsModel).filter(
            GraphicsModel.source == args.set)
        for rec in query.all():
            print(rec.bibcode)
    else:
        sys.exit('Provide --identifier or --set\n')


def cmd_backupdb(session, config, args):
    if not args.set:
        sys.exit('Provide --set\n')
    source = args.set
    backupdirbase = '/proj/ads/articles/graphics/backup'
    backupdir = "%s/%s" % (backupdirbase, source)
    os.makedirs(backupdir, exist_ok=True)
    query = session.query(GraphicsModel).filter(GraphicsModel.source == source)
    for rec in query.all():
        ofile = "%s/%s.json" % (backupdir, rec.bibcode)
        if os.path.exists(ofile):
            continue
        with open(ofile, 'w') as f:
            json.dump(rec, f, cls=AlchemyEncoder, indent=4)


def cmd_updatedb(session, config, args):
    set2journal = config.get('GRAPHICS_PUBSETS', {})
    journal2set = {v: k for k in set2journal for v in set2journal[k]}

    categories = config.get('GRAPHICS_PUBSETS', {}).get('arXiv', [])
    bibstems = list(map(lambda a: re.sub(r"\W", ".", "%-5s" % a), categories))
    category2bibstem = {}
    bibstem2category = {}
    for cat, stem in zip(categories, bibstems):
        category2bibstem[cat] = stem
        bibstem2category[stem] = cat

    now = datetime.now()
    default_year = "%s-%s" % (now.year - 1, now.year + 1)

    identifiers = defaultdict(list)

    if args.identifier:
        is_preprint = True in [args.identifier.find(a) > -1 for a in bibstems]
        sys.stderr.write('Processing %s\n' % args.identifier)
        args.force = True
        for identifier in args.identifier.split(','):
            if identifier[:4].isdigit():
                bibstem = identifier[4:13]
                year = identifier[:4]
                source = ""
                if is_preprint:
                    source = "arXiv"
                    bibstem = "arXiv"
                res = tasks.get_identifiers(bibstem, year, source)
                try:
                    ident = [r for r in res if r['bibcode'] == identifier][0]
                except Exception:
                    ident = {'bibcode': identifier}
            else:
                bibstem = 'arXiv'
                source = 'arXiv'
                if identifier.find('arXiv') > -1:
                    year = "20%s" % identifier.split(':')[1][:2]
                else:
                    yy = identifier.split('/')[1][:2]
                    year = "19%s" % yy if int(yy) > 80 else "20%s" % yy
                res = tasks.get_identifiers(bibstem, year, source)
                try:
                    ident = [r for r in res if identifier in r.values()][0]
                except IndexError:
                    ident = {}
            for pset in set2journal:
                if [j for j in set2journal[pset] if j in identifier]:
                    if pset == 'EDP' and ident.get('bibcode', '')[4:9] == 'ARA&A':
                        continue
                    identifiers[pset].append(ident)
    else:
        year = args.year or default_year
        if args.journal:
            for bibstem in args.journal.split(','):
                sys.stderr.write('Processing %s (%s)\n' % (bibstem, year))
                cat = bibstem2category.get(bibstem, bibstem)
                if cat in journal2set:
                    pset = journal2set[cat]
                    stem = category2bibstem.get(bibstem, bibstem)
                    identifiers[pset] += tasks.get_identifiers(stem, year, pset)
        elif args.set:
            for pset in args.set.split(','):
                pset = pset.strip()
                sys.stderr.write('Processing %s (%s)\n' % (pset, year))
                if pset == 'arXiv':
                    identifiers[pset] = tasks.get_identifiers(pset, year, pset)
                else:
                    for bibstem in set2journal.get(pset, []):
                        identifiers[pset] += tasks.get_identifiers(
                            bibstem, year, pset)

    stime = time.time()
    nrecs = session.query(GraphicsModel).count()
    sys.stderr.write('Number of records (before): %s\n' % nrecs)
    rec_num = 0
    for pset, ids in identifiers.items():
        rec_num += len(ids)
        try:
            process_fn = getattr(tasks, 'process_%s_graphics' % pset)
            process_fn(ids, force=args.force)
        except Exception:
            pass
    nrecs = session.query(GraphicsModel).count()
    sys.stderr.write('Number of records (after): %s\n' % nrecs)
    duration = time.time() - stime
    sys.stderr.write('Processed %s records in %.1f seconds\n' % (rec_num, duration))


def cmd_getdata(session, config, args):
    import shutil
    journal = args.journal
    basedir = os.path.join(config.get('GRAPHICS_MNRAS_SOURCE', ''), journal)
    target = config.get('GRAPHICS_MNRAS_TARGET', '')

    re_vol = re.compile(r'''^(\d{3})\.(\d)$''')
    re_img = re.compile(r'''^s[tl][a-z]+\d+fig[a-z]?\d+\.jpeg$''')

    voldirs = [d for d in os.listdir(basedir) if re_vol.search(d)]
    for voldir in voldirs:
        voln, iss = voldir.split('.')
        vdir = "%s/%s" % (basedir, voldir)
        if not os.path.isdir(vdir):
            continue
        vdir_cont = ["%s/%s" % (vdir, e) for e in os.listdir(vdir)
                     if os.path.isdir("%s/%s" % (vdir, e))]
        number_dirs = [d for d in vdir_cont if os.path.basename(d).isdigit()]
        if len(number_dirs) == 0:
            try:
                image_dir = [d for d in vdir_cont
                             if os.path.basename(d) == 'ImageFiles'][0]
                imgfiles = ["%s/%s" % (image_dir, i)
                            for i in os.listdir(image_dir) if re_img.search(i)]
            except Exception:
                imgfiles = []
        else:
            imgfiles = []
            for ndir in number_dirs:
                image_dir = "%s/ImageFiles" % ndir
                if not os.path.isdir(image_dir):
                    continue
                imgfiles += ["%s/%s" % (image_dir, i)
                             for i in os.listdir(image_dir) if re_img.search(i)]

        tvdir = "%s/%s" % (target, voln)
        os.makedirs(tvdir, exist_ok=True)

        for ifile in imgfiles:
            fname = os.path.basename(ifile)
            artid = re.sub('fig.*', '', fname)
            artdir = "%s/%s" % (tvdir, artid)
            os.makedirs(artdir, exist_ok=True)
            tfile = "%s/%s" % (artdir, fname)
            if os.path.exists(tfile):
                continue
            sys.stderr.write('Creating file: %s\n' % tfile)
            shutil.copy(ifile, tfile)


def cmd_export(session, config, args):
    outdir = config.get('GRAPHICS_EXPORT_DIR', '')
    os.makedirs(outdir, exist_ok=True)

    # --- counts: bibcode -> number of thumbnails ---
    tmp = os.path.join(outdir, 'graphics.tab.tmp')
    with open(tmp, 'w') as fh:
        for rec in (session.query(GraphicsModel)
                    .order_by(GraphicsModel.bibcode).all()):
            try:
                n = len(rec.thumbnails)
            except Exception:
                continue
            fh.write("%s\t%s\n" % (rec.bibcode, n))
    os.rename(tmp, os.path.join(outdir, 'graphics.tab'))
    sys.stderr.write('Wrote graphics.tab\n')

    # --- links: per-source thumbnail + label rows ---
    sources = list(config.get('GRAPHICS_SOURCE_NAMES', {}).keys())
    for source in sources:
        tmp = os.path.join(outdir, '%s.graphics.links.tsv.tmp' % source)
        with open(tmp, 'w') as fh:
            for rec in (session.query(GraphicsModel)
                        .filter(GraphicsModel.source == source).all()):
                for figure in (rec.figures or []):
                    label = figure.get('figure_label', '')
                    for entry in figure.get('images', []):
                        fh.write("%s\t%s\t%s\n" % (
                            rec.bibcode, entry.get('thumbnail', ''), label))
        os.rename(tmp, os.path.join(outdir, '%s.graphics.links.tsv' % source))
        sys.stderr.write('Wrote %s.graphics.links.tsv\n' % source)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Graphics pipeline management')
    subparsers = parser.add_subparsers(dest='command')

    p = subparsers.add_parser('updatedb', help='Update the graphics database')
    p.add_argument('--force', '-f', action='store_true', default=False)
    p.add_argument('--identifier', '-i',
                   help='Comma-separated bibcodes or arXiv IDs')
    p.add_argument('--year', '-y', help='Year or year range (e.g. 2023-2024)')
    p.add_argument('--journal', '-j', help='Comma-separated bibstems')
    p.add_argument('--set', '-s', default='IOP, arXiv',
                   help='Comma-separated publisher sets')

    p = subparsers.add_parser('checkdb', help='Check database entries')
    p.add_argument('--identifier', '-i')
    p.add_argument('--set', '-s')

    p = subparsers.add_parser('backupdb', help='Backup database entries to JSON')
    p.add_argument('--set', '-s', required=True)

    p = subparsers.add_parser('getdata',
                              help='Retrieve MNRAS/MNRASL images from source')
    p.add_argument('journal', choices=['MNRAS', 'MNRASL'])

    subparsers.add_parser('export',
                          help='Export counts and thumbnail links to TSV files')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = load_config()

    if args.command == 'getdata':
        cmd_getdata(None, config, args)
        return

    database_url = config.get('SQLALCHEMY_BINDS', {}).get('graphics')
    if not database_url:
        sys.exit('SQLALCHEMY_BINDS.graphics is not set in config.\n')

    session = get_session(database_url)
    tasks.init(session, config)

    commands = {
        'updatedb': cmd_updatedb,
        'checkdb': cmd_checkdb,
        'backupdb': cmd_backupdb,
        'getdata': cmd_getdata,
        'export': cmd_export,
    }
    commands[args.command](session, config, args)


if __name__ == '__main__':
    main()
