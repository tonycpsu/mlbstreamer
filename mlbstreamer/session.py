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
from six.moves.http_cookiejar import LWPCookieJar
from six import StringIO
import requests
# from requests_toolbelt.utils import dump
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
PLATFORM = "macintosh"

BAM_SDK_VERSION="3.0"

API_KEY_URL= "https://www.mlb.com/tv/g490865/"
API_KEY_RE = re.compile(r'"apiKey":"([^"]+)"')
CLIENT_API_KEY_RE = re.compile(r'"clientApiKey":"([^"]+)"')

TOKEN_URL_TEMPLATE = (
    "https://media-entitlement.mlb.com/jwt"
    "?ipid={ipid}&fingerprint={fingerprint}==&os={platform}&appname=mlbtv_web"
)

GAME_CONTENT_URL_TEMPLATE="http://statsapi.mlb.com/api/v1/game/{game_id}/content"

# GAME_FEED_URL = "http://statsapi.mlb.com/api/v1/game/{game_id}/feed/live"

SCHEDULE_TEMPLATE=(
    "http://statsapi.mlb.com/api/v1/schedule"
    "?sportId={sport_id}&startDate={start}&endDate={end}"
    "&gameType={game_type}&gamePk={game_id}"
    "&teamId={team_id}"
    "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
)

ACCESS_TOKEN_URL = "https://edge.bamgrid.com/token"

STREAM_URL_TEMPLATE="https://edge.svcs.mlb.com/media/{media_id}/scenarios/browser"

AIRINGS_URL_TEMPLATE=(
    "https://search-api-mlbtv.mlb.com/svc/search/v2/graphql/persisted/query/"
    "core/Airings?variables={{%22partnerProgramIds%22%3A[%22{game_id}%22]}}"
)

SESSION_FILE=os.path.join(config.CONFIG_DIR, "session")
COOKIE_FILE=os.path.join(config.CONFIG_DIR, "cookies")
CACHE_FILE=os.path.join(config.CONFIG_DIR, "cache.sqlite")

# Default cache duration to 60 seconds
CACHE_DURATION_SHORT = 60 # 60 seconds
CACHE_DURATION_MEDIUM = 60*60*24 # 1 day
CACHE_DURATION_LONG = 60*60*24*30  # 30 days
CACHE_DURATION_DEFAULT = CACHE_DURATION_SHORT

class MLBSessionException(Exception):
    pass

class MLBSession(object):

    HEADERS = {
        "User-agent": USER_AGENT
    }

    def __init__(
            self,
            username, password,
            api_key=None,
            client_api_key=None,
            token=None,
            access_token=None,
            access_token_expiry=None,
            no_cache=False
    ):

        self.session = requests.Session()
        self.session.cookies = LWPCookieJar()
        if not os.path.exists(COOKIE_FILE):
            self.session.cookies.save(COOKIE_FILE)
        self.session.cookies.load(COOKIE_FILE, ignore_discard=True)
        self.session.headers = self.HEADERS
        self._state = AttrDict([
            ("username", username),
            ("password", password),
            ("api_key", api_key),
            ("client_api_key", client_api_key),
            ("token", token),
            ("access_token", access_token),
            ("access_token_expiry", access_token_expiry)
        ])
        self.no_cache = no_cache
        self._cache_responses = False
        if not os.path.exists(CACHE_FILE):
            self.cache_setup(CACHE_FILE)
        self.conn = sqlite3.connect(CACHE_FILE,
                                    detect_types = sqlite3.PARSE_DECLTYPES)
        self.cursor = self.conn.cursor()
        self.cache_purge()
        self.login()

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

    @classmethod
    def new(cls, **kwargs):
        try:
            return cls.load()
        except:
            return cls(username=config.settings.username,
                       password=config.settings.password,
                       **kwargs)

    @classmethod
    def destroy(cls):
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)

    @classmethod
    def load(cls):
        state = yaml.load(open(SESSION_FILE), Loader=AttrDictYAMLLoader)
        return cls(**state)

    def save(self):
        with open(SESSION_FILE, 'w') as outfile:
            yaml.dump(self._state, outfile, default_flow_style=False)
        self.session.cookies.save(COOKIE_FILE)

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

    def login(self):

        logger.debug("checking for existing log in")

        initial_url = ("https://secure.mlb.com/enterworkflow.do"
                       "?flowId=registration.wizard&c_id=mlb")

        # res = self.get(initial_url)
        # if not res.status_code == 200:
        #     raise MLBSessionException(res.content)

        data = {
            "uri": "/account/login_register.jsp",
            "registrationAction": "identify",
            "emailAddress": self.username,
            "password": self.password,
            "submitButton": ""
        }
        if self.logged_in:
            logger.debug("already logged in")
            return

        logger.debug("attempting new log in")

        login_url = "https://securea.mlb.com/authenticate.do"

        res = self.post(
            login_url,
            data=data,
            headers={"Referer": (initial_url)}
        )

        if not (self.ipid and self.fingerprint):
            raise MLBSessionException("Couldn't get ipid / fingerprint")

        logger.debug("logged in: %s" %(self.ipid))
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


    def get_cookie(self, name):
        return requests.utils.dict_from_cookiejar(self.session.cookies).get(name)

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
                self._state.api_key = API_KEY_RE.search(script.text).groups()[0]
            if script.text and "clientApiKey" in script.text:
                self._state.client_api_key = CLIENT_API_KEY_RE.search(script.text).groups()[0]
        self.save()

    @property
    def token(self):
        logger.debug("getting token")
        if not self._state.token:
            headers = {"x-api-key": self.api_key}

            response = self.get(
                TOKEN_URL_TEMPLATE.format(
                    ipid=self.ipid, fingerprint=self.fingerprint, platform=PLATFORM
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
        logger.debug("getting access token")
        if not self._state.access_token or not self.access_token_expiry or \
                self.access_token_expiry < datetime.now(tz=pytz.UTC):

            try:
                self._state.access_token, self.access_token_expiry = self._get_access_token()
            except requests.exceptions.HTTPError:
                # Clear token and then try to get a new access_token
                self.token = None
                self._state.access_token, self.access_token_expiry = self._get_access_token()

        self.save()
        logger.debug("access_token: %s" %(self._state.access_token))
        return self._state.access_token

    def _get_access_token(self):
        headers = {
            "Authorization": "Bearer %s" % (self.client_api_key),
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": BAM_SDK_VERSION,
            "x-bamsdk-platform": PLATFORM,
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
            ACCESS_TOKEN_URL,
            data=data,
            headers=headers
        )
        response.raise_for_status()
        token_response = response.json()

        token_expiry = datetime.now(tz=pytz.UTC) + \
                       timedelta(seconds=token_response["expires_in"])

        return token_response["access_token"], token_expiry

    def content(self, game_id):

        return self.get(GAME_CONTENT_URL_TEMPLATE.format(game_id=game_id)).json()

    # def feed(self, game_id):

    #     return self.get(GAME_FEED_URL.format(game_id=game_id)).json()

    @memo(region="short")
    def schedule(
            self,
            sport_id=None,
            start=None,
            end=None,
            game_type=None,
            team_id=None,
            game_id=None,
    ):

        logger.debug(
            "getting schedule: %s, %s, %s, %s, %s, %s" %(
                sport_id,
                start,
                end,
                game_type,
                team_id,
                game_id
            )
        )
        url = SCHEDULE_TEMPLATE.format(
            sport_id = sport_id if sport_id else "",
            start = start.strftime("%Y-%m-%d") if start else "",
            end = end.strftime("%Y-%m-%d") if end else "",
            game_type = game_type if game_type else "",
            team_id = team_id if team_id else "",
            game_id = game_id if game_id else ""
        )
        with self.cache_responses_short():
            return self.get(url).json()

    @memo(region="short")
    def get_epgs(self, game_id, title="MLBTV"):
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
                  title="MLBTV",
                  preferred_stream=None,
                  call_letters=None):

        logger.debug("geting media for game %d" %(game_id))

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
                    yield item
            else:
                if len(epg["items"]):
                    logger.debug("using non-preferred stream")
                    yield epg["items"][0]
        # raise StopIteration

    def airings(self, game_id):

        airings_url = AIRINGS_URL_TEMPLATE.format(game_id = game_id)
        airings = self.get(
            airings_url
        ).json()["data"]["Airings"]
        return airings


    def media_timestamps(self, game_id, media_id):

        try:
            airing = next(a for a in self.airings(game_id)
                          if a["mediaId"] == media_id)
        except StopIteration:
            raise MLBSessionException("No airing for media %s" %(media_id))

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

    def get_stream(self, media_id):

        # try:
        #     media = next(self.get_media(game_id))
        # except StopIteration:
        #     logger.debug("no media for stream")
        #     return
        # media_id = media["mediaId"]

        headers={
            "Authorization": self.access_token,
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": "3.0",
            "x-bamsdk-platform": PLATFORM,
            "origin": "https://www.mlb.com"
        }
        stream_url = STREAM_URL_TEMPLATE.format(media_id=media_id)
        logger.info("getting stream %s" %(stream_url))
        stream = self.get(
            stream_url,
            headers=headers
        ).json()
        logger.debug("stream response: %s" %(stream))
        if "errors" in stream and len(stream["errors"]):
            return None
        return stream

__all__ = ["MLBSession", "MLBSessionException"]
