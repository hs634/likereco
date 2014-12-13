from _ast import expr
from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import render_to_response
from django.template.context import RequestContext
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_facebook import exceptions as facebook_exceptions, \
    settings as facebook_settings
from django_facebook.connect import CONNECT_ACTIONS, connect_user
from django_facebook.decorators import facebook_required_lazy
from django_facebook.utils import next_redirect, get_registration_backend, \
    to_bool, error_next_redirect, get_instance_for
from open_facebook import exceptions as open_facebook_exceptions
from open_facebook.utils import send_warning
import logging

try:
    unicode = unicode
except NameError:
    unicode = str

logger = logging.getLogger(__name__)


@csrf_exempt
@facebook_required_lazy
def connect(request, graph):
    '''
    Exception and validation functionality around the _connect view
    Separated this out from _connect to preserve readability
    Don't bother reading this code, skip to _connect for the bit you're interested in :)
    '''
    backend = get_registration_backend()
    context = RequestContext(request)

    # validation to ensure the context processor is enabled
    if not context.get('FACEBOOK_APP_ID'):
        message = 'Please specify a Facebook app id and ensure the context processor is enabled'
        raise ValueError(message)

    try:
        response = _connect(request, graph)
    except open_facebook_exceptions.FacebookUnreachable as e:
        # often triggered when Facebook is slow
        warning_format = u'%s, often caused by Facebook slowdown, error %s'
        warn_message = warning_format % (type(e), str(e))
        send_warning(warn_message, e=e)
        additional_params = dict(fb_error_or_cancel=1)
        response = backend.post_error(request, additional_params)

    return response


def _connect(request, graph):
    '''
    Handles the view logic around connect user
    - (if authenticated) connect the user
    - login
    - register

    We are already covered by the facebook_required_lazy decorator
    So we know we either have a graph and permissions, or the user denied
    the oAuth dialog
    '''
    backend = get_registration_backend()
    context = RequestContext(request)
    connect_facebook = to_bool(request.REQUEST.get('connect_facebook'))

    logger.info('trying to connect using Facebook')
    if graph:
        logger.info('found a graph object')
        converter = get_instance_for('user_conversion', graph)
        authenticated = converter.is_authenticated()
        # Defensive programming :)
        if not authenticated:
            raise ValueError('didnt expect this flow')

        logger.info('Facebook is authenticated')
        facebook_data = converter.facebook_profile_data()
        # either, login register or connect the user
        try:
            action, user = connect_user(
                request, connect_facebook=connect_facebook)
            logger.info('Django facebook performed action: %s', action)
        except facebook_exceptions.IncompleteProfileError as e:
            # show them a registration form to add additional data
            warning_format = u'Incomplete profile data encountered with error %s'
            warn_message = warning_format % unicode(e)
            send_warning(warn_message, e=e,
                         facebook_data=facebook_data)

            context['facebook_mode'] = True
            context['form'] = e.form
            return render_to_response(
                backend.get_registration_template(),
                context_instance=context,
            )
        except facebook_exceptions.AlreadyConnectedError as e:
            user_ids = [u.get_user_id() for u in e.users]
            ids_string = ','.join(map(str, user_ids))
            additional_params = dict(already_connected=ids_string)
            return backend.post_error(request, additional_params)

        response = backend.post_connect(request, user, action)

        if action is CONNECT_ACTIONS.LOGIN:
            pass
        elif action is CONNECT_ACTIONS.CONNECT:
            # connect means an existing account was attached to facebook
            messages.info(request, _("You have connected your account "
                                     "to %s's facebook profile") % facebook_data['name'])
        elif action is CONNECT_ACTIONS.REGISTER:
            # hook for tying in specific post registration functionality
            response.set_cookie('fresh_registration', user.id)
    else:
        # the user denied the request
        additional_params = dict(fb_error_or_cancel='1')
        response = backend.post_error(request, additional_params)

    return response


def disconnect(request):
    '''
    Removes Facebook from the users profile
    And redirects to the specified next page
    '''
    if request.method == 'POST':
        messages.info(
            request, _("You have disconnected your Facebook profile."))
        profile = request.user.get_profile()
        profile.disconnect_facebook()
        profile.save()
    response = next_redirect(request)
    return response


def example(request):
    context = RequestContext(request)
    return render_to_response('django_facebook/example.html', context)


def survey(request):
    context = RequestContext(request)
    return render_to_response('django_facebook/survey/agreement.html', context)

def survey1(request):
    get_data = request.REQUEST
    referral_source = get_data.get('ref', request.session.get('ref', None))
    print "Referral source is ...... "
    print referral_source
    request.session['ref'] = referral_source
    context = RequestContext(request)
    if request.user.is_authenticated():
        experiment = request.user.profile_or_self().experiment()
    else:
        experiment = []
    slide_list = []
    for index, ex in enumerate(experiment):
        slide = {}
        slide['experiment_id'] = ex.id
        slide['movie_id_first'] = ex.facebook_id_first
        slide['movie_id_second'] = ex.facebook_id_second
        slide['style'] = "slide-" + str(index+1)
        slide['page_image_url_1'] = ex.movie_source_first
        slide['page_image_url_2'] = ex.movie_source_second
        slide['page_name_1'] = ex.movie_name_first
        slide['page_name_2'] = ex.movie_name_second
        if index != 0 and index % 4 == 0 and index != len(experiment) - 1:
            slide['ask_why_movie_like'] = "1"
        else:
            slide['ask_why_movie_like'] = "0"
        if index == len(experiment)-1:
            slide['last'] = "1"
        else:
            slide['last'] = "0"
        slide_list.append(slide)
        ex.source = referral_source
        ex.save()
    context['survey_question'] = slide_list


    return render_to_response('django_facebook/survey/index.html', context)

def survey2(request):
    if request.method == 'POST':
        print request.POST
    context = RequestContext(request)
    question_list = []
    for index in range(1,11,1):
        question = {}
        question['style'] = "slide-" + str(index)
        question['id'] = str(index)
        question['question_text'] = "Question - " + str(index)
        if index == 10:
            question['last'] = "1"
        else:
            question['last'] = "0"
        question_list.append(question)
    context['survey_question'] = question_list
    return render_to_response('django_facebook/survey/survey.html', context)

def google_doc_survey(request):
    if request.method == 'POST':
        print request.POST
        from django_facebook.models import FacebookUserExperiment
        data = request.POST
        for key, value in data.iteritems():
            if 'HiddenVarName' in key:
                if "_" in value:
                    experiment_id, movie_id = value.split("_")
                    experiment = FacebookUserExperiment.objects.get(pk=int(experiment_id))
                    experiment.user_clicked = long(movie_id)
                    experiment.save()
            if 'HiddenTextName' in key:
                if "_" in key:
                    experiment_id, temp = key.split("_")
                    experiment = FacebookUserExperiment.objects.get(pk=int(experiment_id))
                    experiment.why_movie_like = value
                    experiment.save()

    if request.user.is_authenticated():
        facebook_id = request.user.profile_or_self().facebook_id
    else:
        facebook_id = None
    context = RequestContext(request)
    context['user_facebook_id'] = facebook_id
    prefilled_url = 'https://docs.google.com/forms/d/1LYNd4PFOTcwqTEkXCai1xYXdjffQUWQ3idOKYzgKjLY/viewform?embedded=true&entry.1245674349='
    iframe_src = prefilled_url + str(facebook_id)
    context['iframe_src'] = iframe_src
    return render_to_response('django_facebook/survey/googledocsurvey.html', context)


def survey3(request):
    context = RequestContext(request)
    return render_to_response('django_facebook/survey/done.html', context)


