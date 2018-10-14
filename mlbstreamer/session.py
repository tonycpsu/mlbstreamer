import logging
logger = logging.getLogger("mlbstreamer")
import os
import re
import base64
import binascii
import json
import sqlite3
import pickle
import functools
from contextlib import contextmanager

import six
from six.moves.http_cookiejar import LWPCookieJar, Cookie
from six import StringIO
import requests
from requests_toolbelt.utils import dump
import lxml
import lxml, lxml.etree
import yaml
from orderedattrdict import AttrDict
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
import pytz
from datetime import datetime, timedelta
import dateutil.parser

from . import config
from . import state
from .state import memo

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.12; rv:56.0) "
              "Gecko/20100101 Firefox/56.0.4")

# Default cache duration to 60 seconds
CACHE_DURATION_SHORT = 60 # 60 seconds
CACHE_DURATION_MEDIUM = 60*60*24 # 1 day
CACHE_DURATION_LONG = 60*60*24*30  # 30 days
CACHE_DURATION_DEFAULT = CACHE_DURATION_SHORT

CACHE_FILE=os.path.join(config.CONFIG_DIR, "cache.sqlite")

class Media(AttrDict):
    pass


class Stream(AttrDict):
    pass


class StreamSessionException(Exception):
    pass

class StreamSession(object):
    """
    Top-level stream session interface

    Individual stream providers can be implemented by inheriting from this class
    and implementing methods for login flow, getting streams, etc.
    """


    # SESSION_FILE=os.path.join(config.CONFIG_DIR, "session")

    HEADERS = {
        "User-agent": USER_AGENT
    }

    def __init__(
            self,
            username, password,
            proxies=None,
            no_cache=False,
            *args, **kwargs
    ):

        self.session = requests.Session()
        self.cookies = LWPCookieJar()
        if not os.path.exists(self.COOKIES_FILE):
            self.cookies.save(self.COOKIES_FILE)
        self.cookies.load(self.COOKIES_FILE, ignore_discard=True)
        self.session.headers = self.HEADERS
        self._state = AttrDict([
            ("username", username),
            ("password", password),
            ("proxies", proxies)
        ])
        self.no_cache = no_cache
        self._cache_responses = False
        if not os.path.exists(CACHE_FILE):
            self.cache_setup(CACHE_FILE)
        self.conn = sqlite3.connect(CACHE_FILE,
                                    detect_types = sqlite3.PARSE_DECLTYPES)
        self.cursor = self.conn.cursor()
        self.cache_purge()
        # if not self.logged_in:
        self.login()
        # logger.debug("already logged in")
            # return



    @classmethod
    def session_type(cls):
        return cls.__name__.replace("StreamSession", "").lower()

    @classmethod
    def _COOKIES_FILE(cls):
        return os.path.join(config.CONFIG_DIR, f"{cls.session_type()}.cookies")

    @property
    def COOKIES_FILE(self):
        return self._COOKIES_FILE()

    @classmethod
    def _SESSION_FILE(cls):
        return os.path.join(config.CONFIG_DIR, f"{cls.session_type()}.session")

    @property
    def SESSION_FILE(self):
        return self._SESSION_FILE()

    @classmethod
    def new(cls, **kwargs):
        try:
            return cls.load(**kwargs)
        except FileNotFoundError:
            logger.trace(f"creating new session: {kwargs}")
            provider = config.settings.profile.providers.get(cls.session_type())
            return cls(username=provider.username,
                       password=provider.password,
                       **kwargs)

    @property
    def cookies(self):
        return self.session.cookies

    @cookies.setter
    def cookies(self, value):
        self.session.cookies = value

    @classmethod
    def destroy(cls):
        if os.path.exists(cls.COOKIES_FILE):
            os.remove(cls.COOKIES_FILE)
        if os.path.exists(cls.SESSION_FILE):
            os.remove(cls.SESSION_FILE)

    @classmethod
    def load(cls, *args, **kwargs):
        state = yaml.load(open(cls._SESSION_FILE()), Loader=AttrDictYAMLLoader)
        logger.trace(f"load: {cls.__name__}, {state}")
        return cls(**state)

    def save(self):
        logger.trace(f"load: {self.__class__.__name__}, {self._state}")
        with open(self.SESSION_FILE, 'w') as outfile:
            yaml.dump(self._state, outfile, default_flow_style=False)
        self.cookies.save(self.COOKIES_FILE)


    def get_cookie(self, name):
        return requests.utils.dict_from_cookiejar(self.cookies).get(name)

    def __getattr__(self, attr):
        if attr in ["delete", "get", "head", "options", "post", "put", "patch"]:
            # return getattr(self.session, attr)
            session_method = getattr(self.session, attr)
            return functools.partial(self.request, session_method)
        # raise AttributeError(attr)

    def request(self, method, url, *args, **kwargs):

        response = None
        use_cache = not self.no_cache and self._cache_responses
        if use_cache:
            logger.debug("getting cached response for %s" %(url))
            self.cursor.execute(
                "SELECT response, last_seen "
                "FROM response_cache "
                "WHERE url = ?",
                (url,)
            )
            try:
                (pickled_response, last_seen) = self.cursor.fetchone()
                td = datetime.now() - last_seen
                if td.seconds >= self._cache_responses:
                    logger.debug("cache expired for %s" %(url))
                else:
                    response = pickle.loads(pickled_response)
                    logger.debug("using cached response for %s" %(url))
            except TypeError:
                logger.debug("no cached response for %s" %(url))

        if not response:
            response = method(url, *args, **kwargs)
            logger.trace(dump.dump_all(response).decode("utf-8"))
        if use_cache:
            pickled_response = pickle.dumps(response)
            sql="""INSERT OR REPLACE
            INTO response_cache (url, response, last_seen)
            VALUES (?, ?, ?)"""
            self.cursor.execute(
                sql,
                (url, pickled_response, datetime.now())
            )
            self.conn.commit()

        return response

    @property
    def username(self):
        return self._state.username

    @property
    def password(self):
        return self._state.password

    @property
    def proxies(self):
        return self._state.proxies

    @property
    def headers(self):
        return []

    @proxies.setter
    def proxies(self, value):
        # Override proxy environment variables if proxies are defined on session
        if value is not None:
            self.session.trust_env = (len(value) == 0)
        self._state.proxies = value
        self.session.proxies.update(value)


    @contextmanager
    def cache_responses(self, duration=CACHE_DURATION_DEFAULT):
        self._cache_responses = duration
        try:
            yield
        finally:
            self._cache_responses = False

    def cache_responses_short(self):
        return self.cache_responses(CACHE_DURATION_SHORT)

    def cache_responses_medium(self):
        return self.cache_responses(CACHE_DURATION_MEDIUM)

    def cache_responses_long(self):
        return self.cache_responses(CACHE_DURATION_LONG)

    def cache_setup(self, dbfile):

        conn = sqlite3.connect(dbfile)
        c = conn.cursor()
        c.execute('''
        CREATE TABLE response_cache
        (url TEXT,
        response TEXT,
        last_seen TIMESTAMP DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (url))''');
        conn.commit()
        c.close()

    def cache_purge(self, days=CACHE_DURATION_LONG):

        self.cursor.execute(
            "DELETE "
            "FROM response_cache "
            "WHERE last_seen < datetime('now', '-%d days')" %(days)
        )

class BAMStreamSessionMixin(object):
    """
    StreamSession subclass for BAMTech Media stream providers, which currently
    includes MLB.tv and NHL.tv
    """
    sport_id = 1 # FIXME

    @memo(region="short")
    def schedule(
            self,
            # sport_id=None,
            start=None,
            end=None,
            game_type=None,
            team_id=None,
            game_id=None,
    ):

        logger.debug(
            "getting schedule: %s, %s, %s, %s, %s, %s" %(
                self.sport_id,
                start,
                end,
                game_type,
                team_id,
                game_id
            )
        )
        url = self.SCHEDULE_TEMPLATE.format(
            sport_id = self.sport_id,
            start = start.strftime("%Y-%m-%d") if start else "",
            end = end.strftime("%Y-%m-%d") if end else "",
            game_type = game_type if game_type else "",
            team_id = team_id if team_id else "",
            game_id = game_id if game_id else ""
        )
        with self.cache_responses_short():
            return self.get(url).json()

    @memo(region="short")
    def get_epgs(self, game_id, title=None):

        schedule = self.schedule(game_id=game_id)
        try:
            # Get last date for games that have been rescheduled to a later date
            game = schedule["dates"][-1]["games"][0]
        except KeyError:
            logger.debug("no game data")
            return
        epgs = game["content"]["media"]["epg"]

        if not isinstance(epgs, list):
            epgs = [epgs]

        return [ e for e in epgs if (not title) or title == e["title"] ]

    def get_media(self,
                  game_id,
                  media_id=None,
                  title=None,
                  preferred_stream=None,
                  call_letters=None):

        logger.debug(f"geting media for game {game_id} ({media_id}, {title}, {call_letters})")

        epgs = self.get_epgs(game_id, title)
        for epg in epgs:
            for item in epg["items"]:
                if (not preferred_stream
                    or (item.get("mediaFeedType", "").lower() == preferred_stream)
                ) and (
                    not call_letters
                    or (item.get("callLetters", "").lower() == call_letters)
                ) and (
                    not media_id
                    or (item.get("mediaId", "").lower() == media_id)
                ):
                    logger.debug("found preferred stream")
                    yield Media(item)
            else:
                if len(epg["items"]):
                    logger.debug("using non-preferred stream")
                    yield Media(epg["items"][0])
        # raise StopIteration



class MLBStreamSession(BAMStreamSessionMixin, StreamSession):

    SCHEDULE_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    PLATFORM = "macintosh"
    BAM_SDK_VERSION = "3.0"

    API_KEY_URL = "https://www.mlb.com/tv/g490865/"

    API_KEY_RE = re.compile(r'"apiKey":"([^"]+)"')

    CLIENT_API_KEY_RE = re.compile(r'"clientApiKey":"([^"]+)"')

    TOKEN_URL_TEMPLATE = (
        "https://media-entitlement.mlb.com/jwt"
        "?ipid={ipid}&fingerprint={fingerprint}==&os={platform}&appname=mlbtv_web"
    )

    GAME_CONTENT_URL_TEMPLATE="http://statsapi.mlb.com/api/v1/game/{game_id}/content"

    ACCESS_TOKEN_URL = "https://edge.bamgrid.com/token"

    STREAM_URL_TEMPLATE="https://edge.svcs.mlb.com/media/{media_id}/scenarios/browser"

    AIRINGS_URL_TEMPLATE=(
        "https://search-api-mlbtv.mlb.com/svc/search/v2/graphql/persisted/query/"
        "core/Airings?variables={{%22partnerProgramIds%22%3A[%22{game_id}%22]}}"
    )

    def __init__(
            self,
            username, password,
            api_key=None,
            client_api_key=None,
            token=None,
            access_token=None,
            access_token_expiry=None,
            *args, **kwargs
    ):
        super(MLBStreamSession, self).__init__(
            username, password,
            *args, **kwargs
        )
        self._state.api_key = api_key
        self._state.client_api_key = client_api_key
        self._state.token = token
        self._state.access_token = access_token
        self._state.access_token_expiry = access_token_expiry


    def login(self):

        if self.logged_in:
            logger.debug("already logged in")
            return

        # logger.debug("checking for existing log in")

        initial_url = ("https://secure.mlb.com/enterworkflow.do"
                       "?flowId=registration.wizard&c_id=mlb")

        # res = self.get(initial_url)
        # if not res.status_code == 200:
        #     raise StreamSessionException(res.content)

        data = {
            "uri": "/account/login_register.jsp",
            "registrationAction": "identify",
            "emailAddress": self.username,
            "password": self.password,
            "submitButton": ""
        }
        logger.debug("attempting new log in")

        login_url = "https://securea.mlb.com/authenticate.do"

        res = self.post(
            login_url,
            data=data,
            headers={"Referer": (initial_url)}
        )

        if not (self.ipid and self.fingerprint):
            raise StreamSessionException("Couldn't get ipid / fingerprint")

        logger.info("logged in: %s" %(self.ipid))
        self.save()

    @property
    def logged_in(self):

        logged_in_url = ("https://web-secure.mlb.com/enterworkflow.do"
                         "?flowId=registration.newsletter&c_id=mlb")
        content = self.get(logged_in_url).text
        parser = lxml.etree.HTMLParser()
        data = lxml.etree.parse(StringIO(content), parser)
        if "Login/Register" in data.xpath(".//title")[0].text:
            return False

    @property
    def headers(self):

        return {
            "Authorization": self.access_token
        }


    @property
    def ipid(self):
        return self.get_cookie("ipid")

    @property
    def fingerprint(self):
        return self.get_cookie("fprt")

    @property
    def api_key(self):

        if not self._state.get("api_key"):
            self.update_api_keys()
        return self._state.api_key

    @property
    def client_api_key(self):

        if not self._state.get("client_api_key"):
            self.update_api_keys()
        return self._state.client_api_key

    def update_api_keys(self):

        logger.debug("updating api keys")
        content = self.get("https://www.mlb.com/tv/g490865/").text
        parser = lxml.etree.HTMLParser()
        data = lxml.etree.parse(StringIO(content), parser)

        scripts = data.xpath(".//script")
        for script in scripts:
            if script.text and "apiKey" in script.text:
                self._state.api_key = self.API_KEY_RE.search(script.text).groups()[0]
            if script.text and "clientApiKey" in script.text:
                self._state.client_api_key = self.CLIENT_API_KEY_RE.search(script.text).groups()[0]
        self.save()

    @property
    def token(self):
        if not self._state.token:
            logger.debug("getting token")
            headers = {"x-api-key": self.api_key}

            response = self.get(
                self.TOKEN_URL_TEMPLATE.format(
                    ipid=self.ipid, fingerprint=self.fingerprint,
                    platform=self.PLATFORM
                ),
                headers=headers
            )
            self._state.token = response.text
        return self._state.token

    @token.setter
    def token(self, value):
        self._state.token = value

    @property
    def access_token_expiry(self):

        if self._state.access_token_expiry:
            return dateutil.parser.parse(self._state.access_token_expiry)

    @access_token_expiry.setter
    def access_token_expiry(self, val):
        if val:
            self._state.access_token_expiry = val.isoformat()

    @property
    def access_token(self):
        if not self._state.access_token or not self.access_token_expiry or \
                self.access_token_expiry < datetime.now(tz=pytz.UTC):
            try:
                self.refresh_access_token()
            except requests.exceptions.HTTPError:
                # Clear token and then try to get a new access_token
                self.refresh_access_token(clear_token=True)

        logger.debug("access_token: %s" %(self._state.access_token))
        return self._state.access_token

    def refresh_access_token(self, clear_token=False):
        logger.debug("refreshing access token")
        if clear_token:
            self.token = None
        headers = {
            "Authorization": "Bearer %s" % (self.client_api_key),
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": self.BAM_SDK_VERSION,
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "platform": "browser",
            "setCookie": "false",
            "subject_token": self.token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt"
        }
        response = self.post(
            self.ACCESS_TOKEN_URL,
            data=data,
            headers=headers
        )
        # from requests_toolbelt.utils import dump
        # print(dump.dump_all(response).decode("utf-8"))
        response.raise_for_status()
        token_response = response.json()

        self.access_token_expiry = datetime.now(tz=pytz.UTC) + \
                       timedelta(seconds=token_response["expires_in"])
        self._state.access_token = token_response["access_token"]
        self.save()

    def content(self, game_id):

        return self.get(
            self.GAME_CONTENT_URL_TEMPLATE.format(game_id=game_id)).json()

    # def feed(self, game_id):

    #     return self.get(GAME_FEED_URL.format(game_id=game_id)).json()

    @memo(region="long")
    def teams(self, sport_code="mlb", season=None):

        if sport_code != "mlb":
            media_title = "MiLBTV"
            raise MLBPlayException("Sorry, MiLB.tv streams are not yet supported")

        sports_url = (
            "http://statsapi.mlb.com/api/v1/sports"
        )
        with state.session.cache_responses_long():
            sports = state.session.get(sports_url).json()

        sport = next(s for s in sports["sports"] if s["code"] == sport_code)

        # season = game_date.year
        teams_url = (
            "http://statsapi.mlb.com/api/v1/teams"
            "?sportId={sport}&{season}".format(
                sport=sport["id"],
                season=season if season else ""
            )
        )

        # raise Exception(state.session.get(teams_url).json())
        with state.session.cache_responses_long():
            teams = AttrDict(
                (team["abbreviation"].lower(), team["id"])
                for team in sorted(state.session.get(teams_url).json()["teams"],
                                   key=lambda t: t["fileCode"])
            )

        return teams

    def airings(self, game_id):

        airings_url = self.AIRINGS_URL_TEMPLATE.format(game_id = game_id)
        airings = self.get(
            airings_url
        ).json()["data"]["Airings"]
        return airings


    def media_timestamps(self, game_id, media_id):

        try:
            airing = next(a for a in self.airings(game_id)
                          if a["mediaId"] == media_id)
        except StopIteration:
            raise StreamSessionException("No airing for media %s" %(media_id))

        start_timestamps = []
        try:
            start_time = next(
                    t["startDatetime"] for t in
                    next(m for m in airing["milestones"]
                     if m["milestoneType"] == "BROADCAST_START"
                    )["milestoneTime"]
                if t["type"] == "absolute"
                )

        except StopIteration:
            # Some streams don't have a "BROADCAST_START" milestone.  We need
            # something, so we use the scheduled game start time, which is
            # probably wrong.
            start_time = airing["startDate"]

        start_timestamps.append(
            ("S", start_time)
        )

        try:
            start_offset = next(
                t["start"] for t in
                next(m for m in airing["milestones"]
                     if m["milestoneType"] == "BROADCAST_START"
                )["milestoneTime"]
                if t["type"] == "offset"
            )
        except StopIteration:
            # Same as above.  Missing BROADCAST_START milestone means we
            # probably don't get accurate offsets for inning milestones.
            start_offset = 0

        start_timestamps.append(
            ("SO", start_offset)
        )

        timestamps = AttrDict(start_timestamps)
        timestamps.update(AttrDict([
            (
            "%s%s" %(
                "T"
                if next(
                        k for k in m["keywords"]
                        if k["type"] == "top"
                )["value"] == "true"
                else "B",
                int(
                    next(
                        k for k in m["keywords"] if k["type"] == "inning"
                    )["value"]
                )),
            next(t["start"]
                      for t in m["milestoneTime"]
                      if t["type"] == "offset"
                 )
            )
                 for m in airing["milestones"]
                 if m["milestoneType"] == "INNING_START"
        ]))
        return timestamps

    def get_stream(self, media):

        media_id = media.get("mediaId", media.get("guid"))

        headers={
            "Authorization": self.access_token,
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": "3.0",
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        stream_url = self.STREAM_URL_TEMPLATE.format(media_id=media_id)
        logger.info("getting stream %s" %(stream_url))
        stream = self.get(
            stream_url,
            headers=headers
        ).json()
        logger.debug("stream response: %s" %(stream))
        if "errors" in stream and len(stream["errors"]):
            return None
        stream = Stream(stream)
        stream.url = stream["stream"]["complete"]
        return stream



class NHLStreamSession(BAMStreamSessionMixin, StreamSession):

    AUTH = b"web_nhl-v1.0.0:2d1d846ea3b194a18ef40ac9fbce97e3"

    SCHEDULE_TEMPLATE = (
        "https://statsapi.web.nhl.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    def __init__(
            self,
            username, password,
            session_key=None,
            *args, **kwargs
    ):
        super(NHLStreamSession, self).__init__(
            username, password,
            *args, **kwargs
        )
        self.session_key = session_key


    def login(self):

        if self.logged_in:
            logger.info("already logged in")
            return

        auth = base64.b64encode(self.AUTH).decode("utf-8")

        token_url = "https://user.svc.nhl.com/oauth/token?grant_type=client_credentials"

        headers = {
            "Authorization": f"Basic {auth}",
            # "Referer": "https://www.nhl.com/login/freeGame?forwardUrl=https%3A%2F%2Fwww.nhl.com%2Ftv%2F2018020013%2F221-2000552%2F61332703",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://www.nhl.com"
        }

        res = self.session.post(token_url, headers=headers)
        self.token = json.loads(res.text)["access_token"]

        login_url="https://gateway.web.nhl.com/ws/subscription/flow/nhlPurchase.login"

        auth = base64.b64encode(b"web_nhl-v1.0.0:2d1d846ea3b194a18ef40ac9fbce97e3")

        params = {
            "nhlCredentials":  {
                "email": self.username,
                "password": self.password
            }
        }

        headers = {
            "Authorization": self.token,
            "Origin": "https://www.nhl.com",
            # "Referer": "https://www.nhl.com/login/freeGame?forwardUrl=https%3A%2F%2Fwww.nhl.com%2Ftv%2F2018020013%2F221-2000552%2F61332703",
        }

        res = self.session.post(
            login_url,
            json=params,
            headers=headers
        )
        self.save()
        return (res.status_code == 200)


    @property
    def logged_in(self):

        logged_in_url = "https://account.nhl.com/ui/AccountProfile"
        content = self.get(logged_in_url).text
        # FIXME: this is gross
        if '"NHL Account - Profile"' in content:
            return True
        return False

    @property
    def session_key(self):
        return self._state.session_key

    @session_key.setter
    def session_key(self, value):
        self._state.session_key = value

    @property
    def token(self):
        return self._state.token

    @token.setter
    def token(self, value):
        self._state.token = value


    @memo(region="long")
    def teams(self, sport_code="mlb", season=None):

        teams_url = (
            "https://statsapi.web.nhl.com/api/v1/teams"
            "?{season}".format(
                season=season if season else ""
            )
        )

        # raise Exception(state.session.get(teams_url).json())
        with state.session.cache_responses_long():
            teams = AttrDict(
                (team["abbreviation"].lower(), team["id"])
                for team in sorted(state.session.get(teams_url).json()["teams"],
                                   key=lambda t: t["abbreviation"])
            )

        return teams


    def get_stream(self, media):

        url = "https://mf.svc.nhl.com/ws/media/mf/v2.4/stream"

        if not self.session_key:
            logger.info("getting session key")

            event_id = media["eventId"]

            params = {
                "eventId": event_id,
                "format": "json",
                "platform": "WEB_MEDIAPLAYER",
                "subject": "NHLTV",
                "_": "1538708097285"
            }

            res = self.get(
                url,
                params=params
            )
            j = res.json()
            logger.trace(json.dumps(j, sort_keys=True,
                             indent=4, separators=(',', ': ')))

            self.session_key = j["session_key"]
            self.save()

        params = {
            "contentId": media["mediaPlaybackId"],
            "playbackScenario": "HTTP_CLOUD_WIRED_WEB",
            "sessionKey": self.session_key,
            "auth": "response",
            "platform": "WEB_MEDIAPLAYER",
            "_": "1538708097285"
        }
        res = self.get(
            url,
            params=params
        )
        j = res.json()
        logger.trace(json.dumps(j, sort_keys=True,
                                   indent=4, separators=(',', ': ')))

        media_auth = next(x["attributeValue"]
                          for x in j["session_info"]["sessionAttributes"]
                          if x["attributeName"] == "mediaAuth_v2")

        self.cookies.set_cookie(
            Cookie(0, 'mediaAuth_v2', media_auth,
                   '80', '80', '.nhl.com',
                   None, None, '/', True, False, 4102444800, None, None, None, {}),
        )

        stream = Stream(j["user_verified_event"][0]["user_verified_content"][0]["user_verified_media_item"][0])

        return stream





def main():

    from . import state
    from . import utils
    import argparse

    global options

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    options, args = parser.parse_known_args()

    utils.setup_logging(options.verbose - options.quiet)

    state.session = MLBStreamSession.new()
    raise Exception(state.session.token)

    # state.session = NHLStreamSession.new()
    # raise Exception(state.session.session_key)


    # schedule = state.session.schedule(game_id=2018020020)
    # media = state.session.get_epgs(game_id=2018020020)
    # print(json.dumps(list(media), sort_keys=True,
    #                  indent=4, separators=(',', ': ')))


if __name__ == "__main__":
    main()



__all__ = ["MLBStreamSession", "StreamSessionException"]
