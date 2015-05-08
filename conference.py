#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
import json
import os
import time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import urlfetch
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session, SessionForm, SessionForms
from models import Wishlist, WishlistForm
from models import ConferenceFormAndSessionForm

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
SAME_SPEAKER_SESSION = ""

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

SFIELDS =  {
            'DURATION': 'duration',
            'DATE': 'date',
            'STARTTIME': 'startTime',
            'TYPEOFSESSION': 'typeOfSession',
            }


CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
    )

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1))

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


def _getUserId():
    """A workaround implementation for getting userid."""
    auth = os.getenv('HTTP_AUTHORIZATION')
    bearer, token = auth.split()
    token_type = 'id_token'
    if 'OAUTH_USER_ID' in os.environ:
        token_type = 'access_token'
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?%s=%s'
           % (token_type, token))
    user = {}
    wait = 1
    for i in range(3):
        resp = urlfetch.fetch(url)
        if resp.status_code == 200:
            user = json.loads(resp.content)
            break
        elif resp.status_code == 400 and 'invalid_token' in resp.content:
            url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?%s=%s'
                   % ('access_token', token))
        else:
            time.sleep(wait)
            wait = wait + i
    return user.get('user_id', '')


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName=None):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, _getUserId()))
        prof = ndb.Key(Profile, _getUserId()).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=" and filtr["operator"] != "!=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = _getUserId()
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/put',
            http_method='GET', name='putAnnouncement')
    def putAnnouncement(self, request):
        """Put Announcement into memcache"""
        return StringMessage(data=self._cacheAnnouncement())


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


#==============session object============

    def _copySessionToForm(self, session):
        """copy model Session to SessionForm"""
        s = SessionForm()
        for field in s.all_fields():
            if hasattr(session, field.name):
                # convert DATE and Time property  to string
                if field.name.endswith('date') or field.name.endswith('Time'):
                    setattr(s, field.name, str(getattr(session, field.name)))
                else:
                    setattr(s, field.name, getattr(session, field.name))
            elif field.name == "websafeSessionKey":
                setattr(s, field.name, session.key.urlsafe())
        s.check_initialized()
        return s

    @staticmethod
    def _memcacheFeaturedSpeaker(same_speaker_s, data):
        """Entry Featured Speaker data to mencache key."""
        # Add current session data.
        s_name = data["sessionName"]
        s_list = [data["sessionName"]]
        # Add other sessions data to string and list.
        for s in same_speaker_s:
            s_list.append(s.sessionName)
            s_name = s_name+","+s.sessionName

        # Genarate the feature string and set the memcache key.
        speaker_session = "Speaker %s has %d sessions in this conference: %s." \
                    %(data["speaker"], len(s_list), s_name)

        memcache.set(SAME_SPEAKER_SESSION, speaker_session)

    @ndb.transactional()
    def _createSessionObject(self, request, conf):
        """Create a new session. Called by endpoints.method createSession.
        Args:
            conf: It should be a Conference Entity.
        """
        # Check if the current user have logined
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        # Check if the current user is the conference owner
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException('You are not the conference organizer.')

        # Check if nameSession is not none.
        if not request.sessionName:
            raise endpoints.BadRequestException("Session 'name' field required")

        # Copy all field in request to data dict. Delete two websafeKey 
        # which won't store in Session.
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']
        del data['websafeSessionKey']

        # Get the other same conference's session, check if there is a same speaker.
        conf_s = self._getConferenceByWebsafekey(request)
        same_speaker_s = conf_s.filter(Session.speaker==data['speaker']).fetch()
        # If yes, set the memcache key.
        if same_speaker_s:
            memkey = self._memcacheFeaturedSpeaker(same_speaker_s, data)

        # Convert 'date' and 'time' from string to datetime format
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()

        # Define field "conferenceBelongTo" to conf name. 
        data['conferenceBelongTo'] = conf.name
        # Set this created session as a child of this conference.
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        data['parent'] = c_key

        s = Session(**data)
        s.put()
        del data['parent']

        return self._copySessionToForm(s)

    @endpoints.method(SESS_POST_REQUEST, SessionForm, path='conferenceCreateSession/{websafeConferenceKey}',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create a new session in a conference."""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        return self._createSessionObject(request, conf)

    def _getConferenceByWebsafekey(self, request):
        """Get conference by websafekey, return all session in this conference."""
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf_s = Session.query(ancestor=c_key)
        return conf_s

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
            path='getConferenceSessions/{websafeConferenceKey}',
            http_method='POST', name='getConferenceSessions')
    def getConferenceSessions(self, request): 
        """Get all sessions of a conference."""
        conf_s = self._getConferenceByWebsafekey(request)
        return SessionForms(
            items=[self._copySessionToForm(s) for s in conf_s]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='getAllSessions',
            http_method='GET', name='getAllSessions')
    def getAllSessions(self, request):
        """Get all sessions."""
        all_s = Session.query().fetch()
        return SessionForms(items=[self._copySessionToForm(s) for s in all_s])

    @endpoints.method(endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1)), SessionForms, 
            path='getSessionsBySpeaker/{speaker}',
            http_method='POST', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Get sessions by speaker."""
        speaker_s = Session.query().filter(Session.speaker==request.speaker).fetch()
        return SessionForms(
            items=[self._copySessionToForm(s) for s in speaker_s])

    @endpoints.method(endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2)), SessionForms,
            path='getConferenceSessionsByType/{websafeConferenceKey}',
            http_method='POST', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Get sessions of a conference by session type. If typeOfSession is empty,
        it will return all session of the conference.
        """
        conf_s = self._getConferenceByWebsafekey(request)
        type_conf_s = conf_s.filter(Session.typeOfSession==request.typeOfSession).fetch()
        return SessionForms(items=[self._copySessionToForm(s) for s in type_conf_s])

#==================wish list=================
    @ndb.transactional(xg=True)
    def _creatWishlist(self, request):
        "Create a Wishlist, add the session to it."
        # Get the current user, and the its name and key.
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()
        user_key = ndb.Key(Profile, user_id)
        user_name = user_key.get().displayName

        # Get the session key and session name.
        session_key =ndb.Key(urlsafe=request.websafeSessionKey)
        session_name = session_key.get().sessionName

        # Store the user and session data in a wishlist entity.
        wishlist = Wishlist(
            userName=user_name,
            userKey=user_key,
            sessionKey=session_key,
            sessionName=session_name)
        wishlist.put()
        
        # Return user and session name as a tuple.
        return user_name, session_name

    @endpoints.method(endpoints.ResourceContainer(
        message_types.VoidMessage, 
        websafeSessionKey=messages.StringField(1)), 
        WishlistForm, path='sesson/{websafeSessionKey}',
        http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to wishlist.
        Args: 
            SessionKey: it should be a Key of a Session entity.
        """
        wishlist = self._creatWishlist(request)
        return WishlistForm(userName=wishlist[0], sessionName=wishlist[1])

    @endpoints.method(SESS_POST_REQUEST, SessionForms,
        path='getSessionsInWishlist/{websafeConferenceKey}',
        http_method='POST', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get user's wishlist's sessions in the conference. """
        
        # Get conference key for urlsafekey.
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)

        # Get current user key.
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()
        user_key = ndb.Key(Profile, user_id)

        # Get wishlist of the user.
        user_wish = Wishlist.query(Wishlist.userKey==user_key).fetch()

        # Put all wishlist sessionkey into wish_s_list.
        wish_s_keys = [w.sessionKey for w in user_wish]

        # Add the sessionkeys which belong to the conference to s_list. 
        s_list = []
        for s_key in wish_s_keys:
            if s_key.parent() == c_key:
                s_list.append(s_key)

        # Get sesssion entity by session key in s_list, and copy to sessionForm. 
        return SessionForms(
            items=[self._copySessionToForm(s.get()) for s in s_list])

#=============task 3====================

    @endpoints.method(endpoints.ResourceContainer(
            message_types.VoidMessage, 
            date=messages.StringField(1)),
            ConferenceFormAndSessionForm,
            path='getConferenceAndSessionByDate', 
            name='getConferenceAndSessionByDate')
    def getConferenceAndSessionByDate(self, request):
        date = request.date
        date = datetime.strptime(date[:10], "%Y-%m-%d").date()
        sameday_c = Conference.query().filter(Conference.startDate <= date and\
            Conference.endDate >= date).fetch()
        sameday_s = Session.query().filter(Session.date==date).fetch()
        return ConferenceFormAndSessionForm( 
            c_data=ConferenceForms(
                items=[self._copyConferenceToForm(c) for c in sameday_c]
                ),
            s_data=SessionForms(
                items=[self._copySessionToForm(s) for s in sameday_s]
                )
            )

    def _keywordFinder(self, data_query, keyword, *fields):
        """Find keyword in ench entity's selected filter. If found,
        the add the entity to list and return it.
        Args:
            data_query: It should be a ndb query.
            keyword: String the user input.
            *fields: Attrabute name of the entity.
        """
        output_list = []
        
        # Get entities from query.
        for item in data_query:
            for f in fields:
                # For the topics field of Conference is list, we handle it separately.
                if f == "topics":
                    for t in getattr(item, f):
                        if t.find(keyword) != -1:
                            output_list.append(item)
                            break
                # Check if the entity has the attrabute.
                if hasattr(item, f):
                    # Check if the attrabute is basestring.
                    if isinstance(getattr(item, f), (str, unicode)):
                        # Check if the keyword in the value, if yes,
                        # put item to output_list and break the for loop.
                        if getattr(item, f).find(keyword) != -1:
                            output_list.append(item)
                            break
        return output_list


    @endpoints.method(endpoints.ResourceContainer(
            message_types.VoidMessage,
            keyword=messages.StringField(1)),
            ConferenceFormAndSessionForm,
            path='getConferenceAndSessionByKeyword', 
            name='getConferenceAndSessionByKeyword')
    def getConferenceAndSessionByKeyword(self, request):
        """Get conference and session by keyword."""
        if not request.keyword:
            raise endpoints.BadRequestException("Keyword required")
        
        keyword = request.keyword

        # Pass all query, keyword, seleted field to function _keywordFinder()
        # get the result list back. 
        all_c = Conference.query().fetch()
        field_c = ['name', 'description', 'topics']
        c_list = self._keywordFinder(all_c, keyword, *field_c)


        all_s = Session.query().fetch()
        field_s = ['sessionName', 'highlights', 'conferenceBelongTo']
        s_list= self._keywordFinder(all_s, keyword, *field_s)

        return ConferenceFormAndSessionForm(
            c_data=ConferenceForms(
                items=[self._copyConferenceToForm(c) for c in c_list]
                ),
            s_data=SessionForms(
                items=[self._copySessionToForm(s) for s in s_list]
                )
            )

    def _getSessionQuery(self, request):
        """Get none workshop, before 19:00 query. """
        # Assume there are three kinds of session type.
        TYPEOFSESSION = ["workshop", "lecture", "keynote"]

        # Covert the time string to time type for comparing.
        startTime = request.startTime
        startTime = datetime.strptime(startTime[:5], "%H:%M").time()

        # Remove the unwanted type from the list.
        typeOfSession = request.typeOfSession
        try:
            TYPEOFSESSION.remove(typeOfSession)
        except ValueError:
            raise endpoints.BadRequestException("%s not in the choice." %typeOfSession)

        # User .IN(list), thus only one inequality filter is used.
        q = Session.query(ndb.AND(Session.typeOfSession.IN(TYPEOFSESSION),\
                    Session.startTime < startTime))
        
        return q

    @endpoints.method(endpoints.ResourceContainer(
            message_types.VoidMessage, 
            typeOfSession=messages.StringField(1),
            startTime=messages.StringField(2)
            ),
            SessionForms,
            path='querySession',
            http_method='POST',
            name='querySession')
    def querySession(self, request):
        """Query for sessions"""
        filter_s = self._getSessionQuery(request)
        return SessionForms(
            items=[self._copySessionToForm(s) for s in filter_s]
            )  

    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='getFeaturedSpeaker',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(SAME_SPEAKER_SESSION) or "")



api = endpoints.api_server([ConferenceApi]) # register API
