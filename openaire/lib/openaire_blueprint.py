# -*- coding: utf-8 -*-
##
## This file is part of Invenio.
## Copyright (C) 2013 CERN.
##
## Invenio is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or (at your option) any later version.
##
## Invenio is distributed in the hope that it will be useful, but
## WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Invenio; if not, write to the Free Software Foundation, Inc.,
## 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

"""OpenAIRE Flask Blueprint"""

from flask import render_template, abort, request, current_app, flash, \
    redirect, url_for, send_file, jsonify
import uuid as uuid_mod
import hashlib
from wtforms import Field
from werkzeug import secure_filename
from invenio.webinterface_handler_flask_utils import InvenioBlueprint, _
from invenio.webuser_flask import current_user
from invenio.bibdocfile import download_external_url
from invenio.openaire_deposit_engine import get_exisiting_publications_for_uid,\
    OpenAIREPublication
from werkzeug.datastructures import MultiDict
import invenio.template
import os
from invenio.openaire_forms import DepositionForm, DepositionFormMapper, PublicationMapper
import json

blueprint = InvenioBlueprint('deposit', __name__,
    url_prefix="/deposit",
    breadcrumbs=[
        (_('Upload'), 'deposit.index'),
    ],
    menubuilder=[
        ('main.deposit', _('Upload'), 'deposit.index', 1),
    ],
)

openaire_deposit_templates = invenio.template.load('openaire_deposit')


# - Wash url arg
# - check redirect to login on guest
# - check error message for logged in user not authorized


@blueprint.route('/', methods=['GET', 'POST'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
@blueprint.invenio_errorpage(template='openaire_error.html', exc_list=(ValueError,))
def index():
    """
    Index page with uploader and list of existing depositions
    """
    return render_template("openaire_index.html",
        title=_('Upload'),
        myresearch=get_exisiting_publications_for_uid(current_user.get_id()),
        pub=None,
    )


@blueprint.route('/upload', methods=['POST', 'GET'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
@blueprint.invenio_wash_urlargd({'pub_id': (unicode, None)})
def upload(pub_id=None):
    """
    PLUpload backend
    """
    if pub_id:
        pub_id = pub_id.encode('utf8')
    uid = current_user.get_id()

    if 'file' not in request.files:
        abort(400)

    afile = request.files['file']
    filename = secure_filename(afile.filename)
    publication = OpenAIREPublication(uid, publicationid=pub_id)
    publication.add_a_fulltext(None, filename, req_file=afile)

    return publication.publicationid


@blueprint.route('/upload/dropbox', methods=['POST'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
@blueprint.invenio_wash_urlargd({'pub_id': (unicode, None), 'fileurl': (unicode, '')})
def dropbox_upload(pub_id=None, fileurl=''):
    """
    Dropbox upload backend
    """
    if pub_id:
        pub_id = pub_id.encode('utf8')
    if fileurl:
        fileurl = fileurl.encode('utf8')

    uid = current_user.get_id()

    if not fileurl:
        abort(400)

    if not fileurl.startswith("https://dl.dropbox.com/"):
        abort(400)

    uploaded_file = download_external_url(fileurl)

    publication = OpenAIREPublication(uid)
    publication.add_a_fulltext(uploaded_file, secure_filename(os.path.basename(fileurl)))

    return redirect(url_for('deposit.edit', pub_id=publication.publicationid))


@blueprint.route('/getfile/<string:pub_id>/<string:file_id>', methods=['GET'])
@blueprint.route('/getfile/<string:pub_id>/<string:file_id>/<string:action>/', methods=['GET'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
def getfile(pub_id='', file_id='', action='view'):
    """
    View for stream file or deleting it.
    """
    pub_id = pub_id.encode('utf8')
    file_id = file_id.encode('utf8')
    action = action.encode('utf8')

    uid = current_user.get_id()

    if action not in ['view', 'delete']:
        abort(404)

    try:
        pub = OpenAIREPublication(uid, pub_id)
        fulltext = pub.fulltexts[file_id]
    except (ValueError, KeyError):
        abort(404)

    if action == 'view':
        return send_file(fulltext.get_full_path(),
                    attachment_filename=fulltext.get_full_name(),
                    as_attachment=True)
    elif action == 'delete':
        if len(pub.fulltexts.keys()) > 1:
            if pub.remove_a_fulltext(file_id):
                flash("File was deleted", category='success')
            else:
                flash("File could not be deleted. Please contact support.", category='danger')
        else:
            flash("File cannot be deleted. You must provide minimum one file.")
        return redirect(url_for('.edit', pub_id=pub.publicationid))


@blueprint.route('/check/', methods=['GET', 'POST'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
def check():
    value = request.args.get('value', '')
    field_name = request.args.get('field', '')
    if field_name == "":
        return "{}"

    form = DepositionForm()
    try:
        field = form._fields[field_name]
        field.process(MultiDict({field_name: value}))
        if field.validate(form):
            return json.dumps({"error_message": "", "error": 0})
        else:
            return json.dumps({"error_message": " ".join(field.errors), "error": 1})
    except (KeyError, AttributeError, TypeError), e:
        return json.dumps({"error_message": unicode(e), "error": 0})


@blueprint.route('/autocomplete/', methods=['GET', 'POST'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
def autocomplete():
    """
        Returns a list with of suggestions for the field based on the current value
    """
    field = request.args.get('field')
    term = request.args.get('term')
    try:
        limit = int(request.args.get('limit'))

        if limit > 0:
            form = DepositionForm()
            field = getattr(form, field)
            val = field.autocomplete(term, limit)
            return json.dumps(val)
    except (AttributeError, ValueError):
        pass
    return json.dumps([])


@blueprint.route('/edit/<string:pub_id>/', methods=['GET', 'POST'])
@blueprint.route('/edit/<string:pub_id>/<string:action>/', methods=['GET', 'POST'])
@blueprint.invenio_force_https
@blueprint.invenio_authenticated
@blueprint.invenio_authorized('submit', doctype='OpenAIRE')
@blueprint.invenio_set_breadcrumb('Edit')
def edit(pub_id=u'', action=u'edit'):
    """
    Edit an upload
    """
    uid = current_user.get_id()

    if action not in ['edit', 'save', 'delete', ]:
        abort(404)

    try:
        pub = OpenAIREPublication(uid, publicationid=pub_id)
        title = pub.metadata.get('title', 'Untitled') or 'Untitled'
    except ValueError:
        abort(404)

    #
    # Action handling
    #
    ctx = {}
    if action == 'delete':
        #
        # Delete action
        #
        pub.delete()
        flash("Upload '%s' was deleted." % title, 'success')
        return redirect(url_for('.index'))
    elif action == 'edit':
        #
        # Edit action
        #
        ctx = {
            'pub': pub,
            'recid': pub.metadata.get('__recid__', None),
            'title': title,
        }

        if request.method == 'POST':
            form = DepositionForm(request.values, crsf_enabled=False)
            mapper = DepositionFormMapper(pub)
            pub = mapper.map(form)
            if form.validate():
                pub.save()
                pub.upload_record()
                return redirect(url_for('deposit.index'))
            else:
                pub.save()
                ctx['form_message'] = "The form was saved, but there were errors. Please see below."
        else:
            mapper = PublicationMapper()
            form = DepositionForm(mapper.map(pub), crsf_enabled=False)

        ctx.update({
            'form': form,
        })

    elif action == 'save':
        #
        # Save action (AjAX)
        #
        if request.method == 'POST':
            form = DepositionForm(request.values, crsf_enabled=False)
            mapper = DepositionFormMapper(pub)
            pub = mapper.map(form)
            if form.validate():
                pub.save()
                return json.dumps({'status': 'success', 'form': 'Successfully saved.'})
            else:
                pub.save()
                errors = dict([(x, '') for x in form._fields.keys()])
                errors.update(form.errors)
                return json.dumps({
                    'status': 'warning',
                    'form': 'The form was saved, but there were errors. Please see below.',
                    'fields': errors,
                })
        else:
            abort(405)

    return render_template(
        "openaire_edit.html",
        myresearch=get_exisiting_publications_for_uid(current_user.get_id()),
        **ctx
    )


# @blueprint.route('/sandbox')
# def sandbox():
#     abort(405)


# @blueprint.route('/checkmetadata')
# def checkmetadata():
#     abort(405)


# @blueprint.route('/ajaxgateway')
# def ajaxgateway():
#     abort(405)


# @blueprint.route('/checksinglefield')
# def checksinglefield():
#     abort(405)


# @blueprint.route('/authorships')
# def authorships():
#     abort(405)


# @blueprint.route('/keywords')
# def keywords():
#     abort(405)