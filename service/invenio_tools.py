# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2010, 2011 CERN.
#
# Invenio is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
import os
import re
import sys
import codecs
import timeout_decorator

MAIN_CAPTION_OR_IMAGE = 0
SUB_CAPTION_OR_IMAGE = 1
CFG_PLOTEXTRACTOR_CONTEXT_EXTRACT_LIMIT = 750
CFG_PLOTEXTRACTOR_CONTEXT_SENTENCE_LIMIT = 2
CFG_PLOTEXTRACTOR_CONTEXT_WORD_LIMIT = 75
CFG_PLOTEXTRACTOR_DISALLOWED_TEX = [
    'begin', 'end', 'section', 'includegraphics', 'caption',
    'acknowledgements',
]

# Python 3: chr() covers the full Unicode range
RE_ALLOWED_XML_1_0_CHARS = re.compile(
    u'[^\U00000009\U0000000A\U0000000D\U00000020-'
    u'\U0000D7FF\U0000E000-\U0000FFFD\U00010000-\U0010FFFF]')
RE_ALLOWED_XML_1_1_CHARS = re.compile(
    u'[^\U00000001-\U0000D7FF\U0000E000-\U0000FFFD\U00010000-\U0010FFFF]')


def extract_captions(tex_file, sdir, image_list, primary=True):
    """
    Take the TeX file and the list of images in the tarball and figure out
    which captions in the text are associated with which images.
    """
    if os.path.isdir(tex_file) or not os.path.exists(tex_file):
        return []
    with open(tex_file, errors='replace') as fd:
        lines = fd.readlines()

    figure_head = '\\begin{figure'
    figure_tail = '\\end{figure'
    picture_head = '\\begin{picture}'
    displaymath_head = '\\begin{displaymath}'
    subfloat_head = '\\subfloat'
    subfig_head = '\\subfigure'
    includegraphics_head = '\\includegraphics'
    epsfig_head = '\\epsfig'
    input_head = '\\input'
    caption_head = '\\caption'
    figcaption_head = '\\figcaption'
    label_head = '\\label'
    eps_tail = '.eps'
    ps_tail = '.ps'
    doc_head = '\\begin{document}'
    doc_tail = '\\end{document}'

    extracted_image_data = []
    cur_image = ''
    caption = ''
    labels = []
    active_label = ""

    if primary:
        for line_index in range(len(lines)):
            if lines[line_index].find(doc_head) < 0:
                lines[line_index] = ''
            else:
                break

    commas_okay = False
    for dummy1, dummy2, filenames in \
            os.walk(os.path.split(os.path.split(tex_file)[0])[0]):
        for filename in filenames:
            if filename.find(',') > -1:
                commas_okay = True
                break

    comment = re.compile("(?<!\\\\)%")

    for line_index in range(len(lines)):
        line = comment.split(lines[line_index])[0]
        line = line.strip()
        lines[line_index] = line

    in_figure_tag = 0

    for line_index in range(len(lines)):
        line = lines[line_index]

        if line == '':
            continue
        if line.find(doc_tail) > -1:
            return extracted_image_data

        index = line.find(figure_head)
        if index > -1:
            in_figure_tag = 1
            cur_image, caption, extracted_image_data = put_it_together(
                cur_image, caption, active_label, extracted_image_data,
                line_index, lines)

        index = max([line.find(eps_tail), line.find(ps_tail),
                     line.find(epsfig_head)])
        if index > -1:
            if line.find(eps_tail) > -1 or line.find(ps_tail) > -1:
                ext = True
            else:
                ext = False
            filenames = intelligently_find_filenames(line, ext=ext,
                                                     commas_okay=commas_okay)
            if line_index < len(lines) - 1:
                filenames.extend(intelligently_find_filenames(
                    lines[line_index + 1], commas_okay=commas_okay))
            if line_index < len(lines) - 2:
                filenames.extend(intelligently_find_filenames(
                    lines[line_index + 2], commas_okay=commas_okay))

            for filename in filenames:
                filename = str(filename)
                if cur_image == '':
                    cur_image = filename
                elif type(cur_image) == list:
                    if type(cur_image[SUB_CAPTION_OR_IMAGE]) == list:
                        cur_image[SUB_CAPTION_OR_IMAGE].append(filename)
                    else:
                        cur_image[SUB_CAPTION_OR_IMAGE] = [filename]
                else:
                    cur_image = ['', [cur_image, filename]]

        index = line.find(includegraphics_head)
        if index > -1:
            open_curly, open_curly_line, close_curly, dummy = \
                find_open_and_close_braces(line_index, index, '{', lines)
            filename = lines[open_curly_line][open_curly + 1:close_curly]
            if cur_image == '':
                cur_image = filename
            elif type(cur_image) == list:
                if type(cur_image[SUB_CAPTION_OR_IMAGE]) == list:
                    cur_image[SUB_CAPTION_OR_IMAGE].append(filename)
                else:
                    cur_image[SUB_CAPTION_OR_IMAGE] = [filename]
            else:
                cur_image = ['', [cur_image, filename]]

        index = line.find(input_head)
        if index > -1:
            new_tex_names = intelligently_find_filenames(line, TeX=True,
                                                         commas_okay=commas_okay)
            for new_tex_name in new_tex_names:
                if new_tex_name != 'ERROR':
                    new_tex_file = get_tex_location(new_tex_name, tex_file)
                    if new_tex_file and primary:
                        extracted_image_data.extend(extract_captions(
                            new_tex_file, sdir, image_list, primary=False))

        index = line.find(picture_head)
        if index > -1:
            pass

        index = line.find(displaymath_head)
        if index > -1:
            pass

        index = max([line.find(caption_head), line.find(figcaption_head)])
        if index > -1:
            open_curly, open_curly_line, close_curly, close_curly_line = \
                find_open_and_close_braces(line_index, index, '{', lines)
            cap_begin = open_curly + 1
            cur_caption = assemble_caption(open_curly_line, cap_begin,
                                           close_curly_line, close_curly, lines)
            if caption == '':
                caption = cur_caption
            elif type(caption) == list:
                if type(caption[SUB_CAPTION_OR_IMAGE]) == list:
                    caption[SUB_CAPTION_OR_IMAGE].append(cur_caption)
                else:
                    caption[SUB_CAPTION_OR_IMAGE] = [cur_caption]
            elif caption != cur_caption:
                caption = ['', [caption, cur_caption]]

        index = line.find(subfloat_head)
        if index > -1:
            if type(cur_image) != list:
                cur_image = [cur_image, []]
            if type(caption) != list:
                caption = [caption, []]

            open_square, open_square_line, close_square, close_square_line = \
                find_open_and_close_braces(line_index, index, '[', lines)
            cap_begin = open_square + 1
            sub_caption = assemble_caption(open_square_line, cap_begin,
                                           close_square_line, close_square, lines)
            caption[SUB_CAPTION_OR_IMAGE].append(sub_caption)

            open_curly, open_curly_line, close_curly, dummy = \
                find_open_and_close_braces(close_square_line,
                                           close_square, '{', lines)
            sub_image = lines[open_curly_line][open_curly + 1:close_curly]
            cur_image[SUB_CAPTION_OR_IMAGE].append(sub_image)

        index = line.find(subfig_head)
        if index > -1:
            if type(cur_image) != list:
                cur_image = [cur_image, []]
            if type(caption) != list:
                caption = [caption, []]

            open_square, open_square_line, close_square, close_square_line = \
                find_open_and_close_braces(line_index, index, '[', lines)
            cap_begin = open_square + 1
            sub_caption = assemble_caption(open_square_line, cap_begin,
                                           close_square_line, close_square, lines)
            caption[SUB_CAPTION_OR_IMAGE].append(sub_caption)

            index_cpy = index
            index = line.find(includegraphics_head)
            while index == -1 and (line_index + 1) < len(lines):
                line_index += 1
                line = lines[line_index]
                index = line.find(includegraphics_head)
            if line_index == len(lines):
                line_index = index_cpy

            open_curly, open_curly_line, close_curly, dummy = \
                find_open_and_close_braces(line_index, index, '{', lines)
            sub_image = lines[open_curly_line][open_curly + 1:close_curly]
            cur_image[SUB_CAPTION_OR_IMAGE].append(sub_image)

        index = line.find(label_head)
        if index > -1 and in_figure_tag:
            open_curly, open_curly_line, close_curly, dummy = \
                find_open_and_close_braces(line_index, index, '{', lines)
            label = lines[open_curly_line][open_curly + 1:close_curly]
            if label not in labels:
                active_label = label
            labels.append(label)

        index = max([line.find(figure_tail), line.find(doc_tail)])
        if index > -1:
            in_figure_tag = 0
            cur_image, caption, extracted_image_data = \
                put_it_together(cur_image, caption, active_label,
                                extracted_image_data, line_index, lines)

        index = line.find(doc_tail)
        if index > -1:
            break

    return extracted_image_data


def put_it_together(cur_image, caption, context, extracted_image_data,
                    line_index, lines):
    if type(cur_image) == list:
        if cur_image[MAIN_CAPTION_OR_IMAGE] == 'ERROR':
            cur_image[MAIN_CAPTION_OR_IMAGE] = ''
        for image in cur_image[SUB_CAPTION_OR_IMAGE]:
            if image == 'ERROR':
                cur_image[SUB_CAPTION_OR_IMAGE].remove(image)

    if cur_image != '' and caption != '':

        if type(cur_image) == list and type(caption) == list:

            if cur_image[MAIN_CAPTION_OR_IMAGE] != '' and \
                    caption[MAIN_CAPTION_OR_IMAGE] != '':
                extracted_image_data.append(
                    (cur_image[MAIN_CAPTION_OR_IMAGE],
                     caption[MAIN_CAPTION_OR_IMAGE],
                     context))
            if type(cur_image[MAIN_CAPTION_OR_IMAGE]) == list:
                cur_image[MAIN_CAPTION_OR_IMAGE] = ''

            if type(cur_image[SUB_CAPTION_OR_IMAGE]) == list:
                if type(caption[SUB_CAPTION_OR_IMAGE]) == list:
                    for index in range(len(cur_image[SUB_CAPTION_OR_IMAGE])):
                        if index < len(caption[SUB_CAPTION_OR_IMAGE]):
                            long_caption = \
                                caption[MAIN_CAPTION_OR_IMAGE] + ' : ' + \
                                caption[SUB_CAPTION_OR_IMAGE][index]
                        else:
                            long_caption = \
                                caption[MAIN_CAPTION_OR_IMAGE] + ' : ' + \
                                'Caption not extracted'
                        extracted_image_data.append(
                            (cur_image[SUB_CAPTION_OR_IMAGE][index],
                             long_caption, context))
                else:
                    long_caption = caption[MAIN_CAPTION_OR_IMAGE] + \
                        ' : ' + caption[SUB_CAPTION_OR_IMAGE]
                    for sub_image in cur_image[SUB_CAPTION_OR_IMAGE]:
                        extracted_image_data.append(
                            (sub_image, long_caption, context))
            else:
                if type(caption[SUB_CAPTION_OR_IMAGE]) == list:
                    long_caption = caption[MAIN_CAPTION_OR_IMAGE]
                    for sub_cap in caption[SUB_CAPTION_OR_IMAGE]:
                        long_caption = long_caption + ' : ' + sub_cap
                    extracted_image_data.append(
                        (cur_image[SUB_CAPTION_OR_IMAGE], long_caption, context))
                else:
                    extracted_image_data.append(
                        (cur_image[SUB_CAPTION_OR_IMAGE],
                         caption[SUB_CAPTION_OR_IMAGE], context))

        elif type(cur_image) == list:
            if cur_image[MAIN_CAPTION_OR_IMAGE] != '':
                extracted_image_data.append(
                    (cur_image[MAIN_CAPTION_OR_IMAGE], caption, context))
            if type(cur_image[SUB_CAPTION_OR_IMAGE]) == list:
                for image in cur_image[SUB_CAPTION_OR_IMAGE]:
                    extracted_image_data.append((image, caption, context))
            else:
                extracted_image_data.append(
                    (cur_image[SUB_CAPTION_OR_IMAGE], caption, context))

        elif type(caption) == list:
            if caption[MAIN_CAPTION_OR_IMAGE] != '':
                extracted_image_data.append(
                    (cur_image, caption[MAIN_CAPTION_OR_IMAGE], context))
            if type(caption[SUB_CAPTION_OR_IMAGE]) == list:
                long_caption = caption[MAIN_CAPTION_OR_IMAGE]
                for subcap in caption[SUB_CAPTION_OR_IMAGE]:
                    if long_caption != '':
                        long_caption += ' : '
                    long_caption += subcap
                extracted_image_data.append((cur_image, long_caption, context))
            else:
                # fixed: was `caption[SUB_CAPTION_OR_IMAGE]. context` (typo in original)
                extracted_image_data.append(
                    (cur_image, caption[SUB_CAPTION_OR_IMAGE], context))

        else:
            extracted_image_data.append((cur_image, caption, context))

    elif cur_image != '' and caption == '':
        REASONABLE_SEARCHBACK = 25
        REASONABLE_SEARCHFORWARD = 5
        curly_no_tag_preceding = '(?<!\\w){'

        for searchback in range(REASONABLE_SEARCHBACK):
            if line_index - searchback < 0:
                continue
            back_line = lines[line_index - searchback]
            m = re.search(curly_no_tag_preceding, back_line)
            if m:
                open_curly = m.start()
                open_curly, open_curly_line, close_curly, \
                    close_curly_line = find_open_and_close_braces(
                        line_index - searchback, open_curly, '{', lines)
                cap_begin = open_curly + 1
                caption = assemble_caption(open_curly_line, cap_begin,
                                           close_curly_line, close_curly, lines)
                if type(cur_image) == list:
                    extracted_image_data.append(
                        (cur_image[MAIN_CAPTION_OR_IMAGE], caption, context))
                    for sub_img in cur_image[SUB_CAPTION_OR_IMAGE]:
                        extracted_image_data.append((sub_img, caption, context))
                else:
                    extracted_image_data.append((cur_image, caption, context))
                    break

        if caption == '':
            for searchforward in range(REASONABLE_SEARCHFORWARD):
                if line_index + searchforward >= len(lines):
                    break
                fwd_line = lines[line_index + searchforward]
                m = re.search(curly_no_tag_preceding, fwd_line)
                if m:
                    open_curly = m.start()
                    open_curly, open_curly_line, close_curly, \
                        close_curly_line = find_open_and_close_braces(
                            line_index + searchforward, open_curly, '{', lines)
                    cap_begin = open_curly + 1
                    caption = assemble_caption(open_curly_line, cap_begin,
                                               close_curly_line, close_curly, lines)
                    if type(cur_image) == list:
                        extracted_image_data.append(
                            (cur_image[MAIN_CAPTION_OR_IMAGE], caption, context))
                        for sub_img in cur_image[SUB_CAPTION_OR_IMAGE]:
                            extracted_image_data.append((sub_img, caption, context))
                    else:
                        extracted_image_data.append((cur_image, caption, context))
                    break

        if caption == '':
            if type(cur_image) == list:
                extracted_image_data.append(
                    (cur_image[MAIN_CAPTION_OR_IMAGE], 'No caption found', context))
                for sub_img in cur_image[SUB_CAPTION_OR_IMAGE]:
                    extracted_image_data.append((sub_img, 'No caption', context))
            else:
                extracted_image_data.append((cur_image, 'No caption found', context))

    elif caption != '' and cur_image == '':
        if type(caption) == list:
            long_caption = caption[MAIN_CAPTION_OR_IMAGE]
            for subcap in caption[SUB_CAPTION_OR_IMAGE]:
                long_caption = long_caption + ': ' + subcap
        else:
            long_caption = caption
        extracted_image_data.append(('', 'noimg' + long_caption, context))

    cur_image = ''
    caption = ''
    return cur_image, caption, extracted_image_data


def intelligently_find_filenames(line, TeX=False, ext=False, commas_okay=False):
    files_included = ['ERROR']

    if commas_okay:
        valid_for_filename = '\\s*[A-Za-z0-9\\-\\=\\+/\\\\_\\.,%#]+'
    else:
        valid_for_filename = '\\s*[A-Za-z0-9\\-\\=\\+/\\\\_\\.%#]+'

    if ext:
        valid_for_filename += r'\.e*ps[texfi2]*'
    if TeX:
        valid_for_filename += r'[\.latex]*'

    file_inclusion = re.findall('=' + valid_for_filename + '[ ,]', line)
    if len(file_inclusion) > 0:
        for file_included in file_inclusion:
            files_included.append(file_included[1:-1])

    file_inclusion = re.findall('(?:[ps]*file=|figure=)' +
                                valid_for_filename + '[,\\]} ]*', line)
    if len(file_inclusion) > 0:
        for file_included in file_inclusion:
            part_before_equals = file_included.split('=')[0]
            if len(part_before_equals) != file_included:
                file_included = file_included[len(part_before_equals) + 1:].strip()
            if file_included not in files_included:
                files_included.append(file_included)

    file_inclusion = re.findall('["\'{\\[]' + valid_for_filename + '[}\\],"\']', line)
    if len(file_inclusion) > 0:
        for file_included in file_inclusion:
            file_included = file_included[1:-1].strip()
            if file_included not in files_included:
                files_included.append(file_included)

    file_inclusion = re.findall('^' + valid_for_filename + '$', line)
    if len(file_inclusion) > 0:
        for file_included in file_inclusion:
            file_included = file_included.strip()
            if file_included not in files_included:
                files_included.append(file_included)

    file_inclusion = re.findall('^' + valid_for_filename + '[,\\} $]', line)
    if len(file_inclusion) > 0:
        for file_included in file_inclusion:
            file_included = file_included.strip()
            if file_included not in files_included:
                files_included.append(file_included)

    file_inclusion = re.findall('\\s*' + valid_for_filename + '\\s*$', line)
    if len(file_inclusion) > 0:
        for file_included in file_inclusion:
            file_included = file_included.strip()
            if file_included not in files_included:
                files_included.append(file_included)

    if files_included != ['ERROR']:
        files_included = files_included[1:]

    for file_included in files_included:
        if file_included == '':
            files_included.remove(file_included)
        if ' ' in file_included:
            for subfile in file_included.split(' '):
                if subfile not in files_included:
                    files_included.append(subfile)
        if ',' in file_included:
            for subfile in file_included.split(' '):
                if subfile not in files_included:
                    files_included.append(subfile)

    return files_included


def find_open_and_close_braces(line_index, start, brace, lines):
    if brace in ['[', ']']:
        open_brace = '['
        close_brace = ']'
    elif brace in ['{', '}']:
        open_brace = '{'
        close_brace = '}'
    elif brace in ['(', ')']:
        open_brace = '('
        close_brace = ')'
    else:
        return (-1, -1, -1, -1)

    open_braces = []
    line = lines[line_index]

    ret_open_index = line.find(open_brace, start)
    line_index_cpy = line_index
    while ret_open_index == -1:
        line_index = line_index + 1
        if line_index >= len(lines):
            return (0, line_index_cpy, 0, line_index_cpy)
        line = lines[line_index]
        ret_open_index = line.find(open_brace)

    open_braces.append(open_brace)
    ret_open_line = line_index
    open_index = ret_open_index
    close_index = ret_open_index

    while len(open_braces) > 0:
        if open_index == -1 and close_index == -1:
            line_index = line_index + 1
            if line_index >= len(lines):
                return (ret_open_index, ret_open_line, ret_open_index, ret_open_line)
            line = lines[line_index]
            close_index = line.find(close_brace)
            open_index = line.find(open_brace)
        else:
            if close_index != -1:
                close_index = line.find(close_brace, close_index + 1)
            if open_index != -1:
                open_index = line.find(open_brace, open_index + 1)

        if close_index != -1:
            open_braces.pop()
            if len(open_braces) == 0 and \
                    (open_index > close_index or open_index == -1):
                break
        if open_index != -1:
            open_braces.append(open_brace)

    ret_close_index = close_index
    return (ret_open_index, ret_open_line, ret_close_index, line_index)


def assemble_caption(begin_line, begin_index, end_line, end_index, lines):
    label_head = '\\label{'

    if end_line > begin_line:
        caption = lines[begin_line][begin_index:]
        for included_line_index in range(begin_line + 1, end_line):
            caption = caption + ' ' + lines[included_line_index]
        caption = caption + ' ' + lines[end_line][:end_index]
        caption = caption.replace('\n', ' ')
        caption = caption.replace('  ', ' ')
    else:
        caption = lines[begin_line][begin_index:end_index]

    label_begin = caption.find(label_head)
    if label_begin > -1:
        dummy_start, dummy_start_line, label_end, dummy_end = \
            find_open_and_close_braces(0, label_begin, '{', [caption])
        caption = caption[:label_begin] + caption[label_end + 1:]

    try:
        caption = wash_for_utf8(caption)
        caption = encode_for_xml(caption, wash=True)
    except Exception:
        caption = caption.replace('&', '&amp;').replace('<', '&lt;')
        caption = caption.replace('>', '&gt;')

    caption = caption.strip()

    if len(caption) > 1 and caption[0] == '{' and caption[-1] == '}':
        caption = caption[1:-1]

    return caption


def get_image_location(image, sdir, image_list, recurred=False):
    if type(image) == list:
        return None

    image = str(image).strip()

    figure_or_file = '(figure=|file=)'
    figure_or_file_in_image = re.findall(figure_or_file, image)
    if len(figure_or_file_in_image) > 0:
        image.replace(figure_or_file_in_image[0], '')
    includegraphics = '\\includegraphics{'
    includegraphics_in_image = re.findall(includegraphics, image)
    if len(includegraphics_in_image) > 0:
        image.replace(includegraphics_in_image[0], '')

    image = image.strip()
    some_kind_of_tag = '\\\\\\w+ '

    if image.startswith('./'):
        image = image[2:]
    if re.match(some_kind_of_tag, image):
        image = image[len(image.split(' ')[0]) + 1:]
    if image.startswith('='):
        image = image[1:]
    if len(image) == 1:
        return None

    image = image.strip()
    image_path = os.path.join(sdir, image)
    converted_image_should_be = get_converted_image_name(image_path)

    if image_list is None:
        image_list = os.listdir(sdir)

    for png_image in image_list:
        if converted_image_should_be == png_image:
            return png_image

    for subdir_name in ['eps', 'fig', 'figs', 'Figures', 'Figs']:
        subdir_path = os.path.join(sdir, subdir_name)
        if os.path.isdir(subdir_path):
            for png_image in os.listdir(subdir_path):
                if converted_image_should_be == png_image:
                    return os.path.join(subdir_name, png_image)

    for png_image in os.listdir(sdir):
        if os.path.split(converted_image_should_be)[-1] == png_image:
            return converted_image_should_be
        if os.path.isdir(os.path.join(sdir, png_image)):
            sub_dir = os.path.join(sdir, png_image)
            for sub_dir_file in os.listdir(sub_dir):
                if os.path.split(converted_image_should_be)[-1] == sub_dir_file:
                    return converted_image_should_be

    for png_image in os.listdir(os.path.split(sdir)[0]):
        if os.path.split(converted_image_should_be)[-1] == png_image:
            return converted_image_should_be
    for png_image in os.listdir(os.path.split(os.path.split(sdir)[0])[0]):
        if os.path.split(converted_image_should_be)[-1] == png_image:
            return converted_image_should_be

    if recurred:
        return None

    for piece in image.split(' '):
        res = get_image_location(piece, sdir, image_list, recurred=True)
        if res is not None:
            return res
    for piece in image.split(','):
        res = get_image_location(piece, sdir, image_list, recurred=True)
        if res is not None:
            return res
    for piece in image.split('='):
        res = get_image_location(piece, sdir, image_list, recurred=True)
        if res is not None:
            return res

    return None


def get_tex_location(new_tex_name, current_tex_name, recurred=False):
    tex_location = None
    current_dir = os.path.split(current_tex_name)[0]
    some_kind_of_tag = '\\\\\\w+ '

    new_tex_name = new_tex_name.strip()
    if new_tex_name.startswith('input'):
        new_tex_name = new_tex_name[len('input'):]
    if re.match(some_kind_of_tag, new_tex_name):
        new_tex_name = new_tex_name[len(new_tex_name.split(' ')[0]) + 1:]
    if new_tex_name.startswith('./'):
        new_tex_name = new_tex_name[2:]
    if len(new_tex_name) == 0:
        return None
    new_tex_name = new_tex_name.strip()

    new_tex_file = os.path.split(new_tex_name)[-1]
    new_tex_folder = os.path.split(new_tex_name)[0]
    if new_tex_folder == new_tex_file:
        new_tex_folder = ''

    for any_file in os.listdir(current_dir):
        if any_file == new_tex_file:
            return os.path.join(current_dir, new_tex_file)

    if os.path.isdir(os.path.join(current_dir, new_tex_folder)):
        for any_file in os.listdir(os.path.join(current_dir, new_tex_folder)):
            if any_file == new_tex_file:
                return os.path.join(os.path.join(current_dir, new_tex_folder),
                                    new_tex_file)

    one_dir_up = os.path.join(os.path.split(current_dir)[0], new_tex_folder)
    if os.path.isdir(one_dir_up):
        for any_file in os.listdir(one_dir_up):
            if any_file == new_tex_file:
                return os.path.join(one_dir_up, new_tex_file)

    two_dirs_up = os.path.join(
        os.path.split(os.path.split(current_dir)[0])[0], new_tex_folder)
    if os.path.isdir(two_dirs_up):
        for any_file in os.listdir(two_dirs_up):
            if any_file == new_tex_file:
                return os.path.join(two_dirs_up, new_tex_file)

    if tex_location is None and not recurred:
        return get_tex_location(new_tex_name + '.tex', current_tex_name,
                                recurred=True)

    return tex_location


def wash_for_utf8(text, correct=True):
    """Ensure text is a str; decode bytes if needed."""
    if isinstance(text, bytes):
        errors = "ignore" if correct else "strict"
        return text.decode("utf-8", errors)
    return text


def encode_for_xml(text, wash=False, xml_version='1.0', quote=False):
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'ignore')
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    if quote:
        text = text.replace('"', '&quot;')
    if wash:
        text = wash_for_xml(text, xml_version=xml_version)
    return text


def wash_for_xml(text, xml_version='1.0'):
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'ignore')
    if xml_version == '1.0':
        return RE_ALLOWED_XML_1_0_CHARS.sub('', text)
    else:
        return RE_ALLOWED_XML_1_1_CHARS.sub('', text)


def get_converted_image_name(image):
    png_extension = '.png'

    if image[(0 - len(png_extension)):] == png_extension:
        return image

    img_dir = os.path.split(image)[0]
    image = os.path.split(image)[-1]

    if len(image.split('.')) > 1:
        old_extension = '.' + image.split('.')[-1]
        if image.split('.')[-1].isdigit():
            converted_image = image + png_extension
        else:
            converted_image = image[:(0 - len(old_extension))] + png_extension
    else:
        converted_image = image + png_extension

    return os.path.join(img_dir, converted_image)


def remove_dups(extracted_image_data):
    img_list = {}
    pared_image_data = []

    for (image, caption, label, contexts) in extracted_image_data:
        if image in img_list:
            if caption not in img_list[image]:
                img_list[image].append(caption)
        else:
            img_list[image] = [caption]

    for (image, caption, label, contexts) in extracted_image_data:
        if image in img_list:
            pared_image_data.append((image,
                                     ' : '.join(img_list[image]), label, contexts))
            del img_list[image]

    return pared_image_data


def prepare_image_data(extracted_image_data, tex_file, image_list):
    sdir = os.path.split(tex_file)[0]
    image_locs_and_captions_and_labels = []
    for (image, caption, label) in extracted_image_data:
        if image == 'ERROR':
            continue
        if image != '':
            image_loc = get_image_location(image, sdir, image_list)
            if image_loc is not None and os.path.exists(image_loc):
                image_locs_and_captions_and_labels.append(
                    (image_loc, caption, label))
        else:
            image_locs_and_captions_and_labels.append((image, caption, label))
    return image_locs_and_captions_and_labels


def extract_context(tex_file, extracted_image_data):
    if os.path.isdir(tex_file) or not os.path.exists(tex_file):
        return []
    with open(tex_file, errors='replace') as fd:
        lines = fd.read()

    new_image_data = []
    for image, caption, label in extracted_image_data:
        context_list = []
        indicies = [match.span()
                    for match in re.finditer(
                        r"(\\(?:fig|ref)\{%s\})" % (re.escape(label),), lines)]
        for startindex, endindex in indicies:
            i = startindex - CFG_PLOTEXTRACTOR_CONTEXT_EXTRACT_LIMIT
            text_before = lines[max(0, i):startindex]
            context_before = get_context(text_before, backwards=True)

            i = endindex + CFG_PLOTEXTRACTOR_CONTEXT_EXTRACT_LIMIT
            text_after = lines[endindex:i]
            context_after = get_context(text_after)
            context_list.append(
                context_before + ' \\ref{' + label + '} ' + context_after)
        new_image_data.append((image, caption, label, context_list))
    return new_image_data


def get_context(lines, backwards=False):
    tex_tag = re.compile(r".*\\(\w+).*")
    sentence = re.compile(r"(?<=[.?!])[\s]+(?=[A-Z])")
    context = []

    word_list = lines.split()
    if backwards:
        word_list.reverse()

    for word in word_list:
        if len(context) >= CFG_PLOTEXTRACTOR_CONTEXT_WORD_LIMIT:
            break
        match = tex_tag.match(word)
        if match and match.group(1) in CFG_PLOTEXTRACTOR_DISALLOWED_TEX:
            if backwards:
                temp_word = ""
                while len(context):
                    temp_word = context.pop()
                    if '}' in temp_word:
                        break
            break
        context.append(word)

    if backwards:
        context.reverse()
    text = " ".join(context)
    sentence_list = sentence.split(text)

    if backwards:
        sentence_list.reverse()

    if len(sentence_list) > CFG_PLOTEXTRACTOR_CONTEXT_SENTENCE_LIMIT:
        return " ".join(sentence_list[:CFG_PLOTEXTRACTOR_CONTEXT_SENTENCE_LIMIT])
    else:
        return " ".join(sentence_list)