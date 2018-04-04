import logging
logger = logging.getLogger("mlbstreamer")
import os
import re
import base64
import binascii
import json

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
import datetime
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

SESSION_FILE=os.path.join(config.CONFIG_DIR, "session")
COOKIE_FILE=os.path.join(config.CONFIG_DIR, "cookies")

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
            access_token_expiry=None
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
        self.login()

    def __getattr__(self, attr):
        if attr in ["delete", "get", "head", "options", "post", "put", "patch"]:
            return getattr(self.session, attr)
        raise AttributeError(attr)

    @property
    def username(self):
        return self._state.username

    @property
    def password(self):
        return self._state.password

    @classmethod
    def new(cls):
        try:
            return cls.load()
        except:
            return cls(username=config.settings.username,
                       password=config.settings.password)

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

    def login(self):

        logger.debug("checking for existing log in")

        initial_url = ("https://secure.mlb.com/enterworkflow.do"
                       "?flowId=registration.wizard&c_id=mlb")

        # res = self.session.get(initial_url)
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

        res = self.session.post(
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
        content = self.session.get(logged_in_url).text
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
        content = self.session.get("https://www.mlb.com/tv/g490865/").text
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

            response = self.session.get(
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
                self.access_token_expiry < datetime.datetime.now(tz=pytz.UTC):

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
        response = self.session.post(
            ACCESS_TOKEN_URL,
            data=data,
            headers=headers
        )
        response.raise_for_status()
        token_response = response.json()

        token_expiry = datetime.datetime.now(tz=pytz.UTC) + \
                       datetime.timedelta(seconds=token_response["expires_in"])

        return token_response["access_token"], token_expiry

    def content(self, game_id):

        return self.session.get(GAME_CONTENT_URL_TEMPLATE.format(game_id=game_id)).json()

    # def feed(self, game_id):

    #     return self.session.get(GAME_FEED_URL.format(game_id=game_id)).json()

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
        return self.session.get(url).json()

    @memo(region="short")
    def get_media(self, game_id,
                  title="MLBTV",
                  preferred_stream=None):

        logger.debug("geting media for game %d" %(game_id))
        schedule = self.schedule(game_id=game_id)
        # raise Exception(schedule)
        try:
            # Get last date for games that have been rescheduled to a later date
            game = schedule["dates"][-1]["games"][0]
        except KeyError:
            logger.debug("no game data")
            return
        for epg in game["content"]["media"]["epg"]:
            if title in [None, epg["title"]]:
                for item in epg["items"]:
                    if preferred_stream in [None, item["mediaFeedType"]]:
                        logger.debug("found preferred stream")
                        yield item
                else:
                    if len(epg["items"]):
                        logger.debug("using non-preferred stream")
                        yield epg["items"][0]
        # raise StopIteration

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
        stream = self.session.get(
            STREAM_URL_TEMPLATE.format(media_id=media_id),
            headers=headers
        ).json()
        logger.debug("stream response: %s" %(stream))
        if "errors" in stream and len(stream["errors"]):
            return None
        return stream

__all__ = ["MLBSession", "MLBSessionException"]
