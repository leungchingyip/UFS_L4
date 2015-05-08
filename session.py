@endpoints.method(SessionQueryForms, SessionForms,
        path='querySession',
        http_method='POST',
        name='querySession')
def querySession(self, request):
    """Query for sessions"""
    filter_s = self._getSessionQuery(request)

    return SessionForms(
        items=[self._copySessionToForm(s) for s in filter_s]
        )  

@endpoints.method(endpoints.ResourceContainer(
        message_types.VoidMessage, 
        date=messages.StringField(1)),
        ConferenceFores, SessionForms,
        path='getSessionsByDate', name='getSessionByDate')
def getSessionByDate(self, request):
    date = request.date
    date = datetime.strptime(date[:10], "%Y-%m-%d").date()
    sameday_c = Conference.query().filter(Conference.startDate <= date and\
        Conference.endDate >= date).fetch()
    sameday_s = Session.query().filter(Session.date==date).fetch()
    return ConferenceFores(
        items=[self._copyConferenceToForm(c) for c in sameday_c]
        ),
        SessionForms(
            items=[self._copySessionToForm(s) for s in sameday_s]
            )

#===wish list

class Wishlist(ndb.Model):
    userName = ndb.StringProperty()
    userKey = ndb.KeyProperty(kind=Profile)
    sessionName = ndb.StringProperty()
    sessionKey = ndb.KeyProperty(kind=Session)

class WishlistForm(messages.Message)
    userName = messages.StringProperty(1)
    sessionName = messages.StringProperty(2)

def _creatWishlist(self, request):
    user = endpoints.get_current_user()
    if not user:
        raise endpoints.UnauthorizedException('Authorization required')
    user_id = _getUserid
    user_key = ndb.Key(Profile, user_id)
    user_name = user_key.get().displayName

    session_key =ndb.Key(urlsafe=request.websafeSessionKey)
    session_name = session_key.get().sessionName

    wishlist = Wishlist(
        userName=user_name
        userKey=user_key,
        sessionKey=session_key,
        sessionName=session_name)

    wishlist.put()
    return user_name, session_name

@endpoints.method(message_types.VoidMessage, WishlistForm,
    path='sesson/{websafeSessionKey}',
    http_method='POST', name='addSessionToWishlist')
def addSessionToWishlist(self, request):
    """Add session to wishlist.
    Args: 
        SessionKey: it should be a Key of a Session entity.
    """
    wishlist = self._creatWishlist(request)
    return WishlistForm(userName, sessionName = wishlist)

@endpoints.method(SESS_POST_REQUEST, SessionForms,
    path='getSessionsInWishlist/{websafeConferenceKey}',
    http_method='POST', name='getSessionsInWishlist')
def getSessionsInWishlist(self, request):
    c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
    
    user = endpoints.get_current_user()
    if not user:
        raise endpoints.UnauthorizedException('Authorization required')
    user_id = _getUserid
    user_key = ndb.Key(Profile, user_id)

    user_wish = Wishlist.query(userKey=user_key).fetch()

    wish_s_keys = [w.sessionKey for w in user_wish]

    s_list = []

    for s_key in wish_s_keys:
        if s_key.parent() == c_key:
            s_list.appand(s_key)

    return SessionForms(
        items=[self._copySessionToForm(s.get()) for s in s_list])






         




# models
from datetime import datetime 

class Session(ndb.Model):
	sesssionName = ndb.StringProperty(required=True)
	highlights = ndb.StringProperty()
	speaker = ndb.StringProperty()
	duration = ndb.NumberProperty()
	typeOfSession = ndb.StringProperty()
	date = ndb.DateProperty()
	startTime = ndb.TimeProperty()
	organizerUserId = ndb.StringProperty()
	conferenceBelongTo = ndb.StringField()


class SessionForm(messages.Message):
	sesssionName = messages.StringProperty(1)
	highlights = messages.StringProperty(2)
	speaker = messages.StringProperty(3)
	duration = messages.NumberProperty(4)
	typeOfSession = messages.StringProperty(5)
	date = messages.StringProperty(6)
	startTime = messages.StringProperty(7)
	websafeKey = messages.StringField(8)
	organizerUserId = messages.StringProperty(9)
    conferenceBelongTo = messages.StringField(10)

    def _copySessionToForm(self, session):
    	s = SessionForm()
    	for field in s.all_fields():
    		if hasattr(session, field.name):
    			# convert DATE and Time property  to string
    			if field.name.endswith('Date') or field.name.endswith('Time'):
    				setattr(s, field.name, str(getattr(session, field.name)))
    			else:
    				setattr(s, field.name, getattr(session, field.name))
    		elif field.name == "websafeKey":
    			setattr(s, field.name, session.key.urlsafe())
    	s.check_initialized()
    	return s

    def _createSessionObject(self, request, conf):
    	user = endpoints.get_current_user()
    	if not user:
    		raise endpoints.UnauthorizedException('Authorization required')
    	user_id = _getUserid()

    	if user_id != conf.organizerUserId:
    		raise endpoints.UnauthorizedException('You are not the conference organizer.')

    	if not request.name:
    		rai endpoints.BadRequestException("Session 'name' field required")

    	data = {field.name: getattr(request, field.name) for field in request.all_fields()}
    	del data['websafeKey']

    	#convert 'date' and 'time' from string to datetiem format
    	if data['date']:
    		data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
    	if data['startTime']:
    		data['startTime'] = datetime.strptime(data['startTime'][:5], "%H, %M").time()

    	data['conferenceBelongTo'] = conf.name
    	c_key = conf.key()
    	s_id = Session.allocate_ids(size=1, parent=c_key)
    	s_key = ndb.Key(Session, s_id, parent=c_key)

    	Session(**data).put()
    	taskqueue.add(params={'email': user.email(),
    		'conferenceInfo':repr(request)},
    		url='/tasks/send_confirmation_email'
    		)
    	return request

    	@endpoints.method(SessionForm, SessionForm, path='conference/{websafeConferenceKey}',
    			http_method='POST', name='createSession')
    	def createSession(self, request):
    		conf = ndb.Key(Conference, request.websafeConferenceKey).get()
    		return self._createSessionObject(request, conf)

	    def _getConferenceSession(self, websafeConferenceKey):
		c_key = ndb.Key(Conference, websafeConferenceKey)
		return conf_s = Session.query(ancestor=c_key)

    	@endpoints.method(message_types.VoidMessage, SessionForm,
    			path='getConferenceSessions/{websafeConferenceKey}',
    			http_method='GET', name='getConferenceSessions')
    	def getConferenceSessions(self, request):		
    		conf_s = self._getConferenceSession(request.websafeConferenceKey)
    		return SessionForm(
    			items=[self._copySessionToForm(s) for s in conf_s]
    		)

    	@endpoints.method(messager_types.VoidMessage, SessionForm, 
    			path='getSessionsBySpeaker/{speaker}',
    			http_method='POST', name='getSessionsBySpeaker')
    	def getSessionsBySpeaker(self, request):
    		speaker_s = Session.query.filter(speaker=request.speaker).fetch_all()
    		return SessionForm(
    			items=[self._copySessionToForm(s) for s in speaker_s])

    	@endpoints.method(messager_types.VoidMessage, SessionForm,
    			path='getConferenceSessions/{websafeConferenceKey}/{sessionType}',
    			http_method='POST', nema='getConferenceSessionsByType')
		def getConferenceSessionsByType(self, request):
			conf_s = self._getConferenceSession(request.websafeConferenceKey)
			type_conf_s = conf_s.filter(typeOfSession=request.sessionType).fetch_all()
			return SessionForm(items=[self._copySessionToForm(s) for s in type_conf_s])













