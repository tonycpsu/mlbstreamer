import logging
logger = logging.getLogger(__name__)

import os.path
import sys
import re
import subprocess
import argparse
import base64
import binascii
import json
from datetime import datetime

import six
from six import StringIO
from six.moves.http_cookiejar import LWPCookieJar
import requests
import lxml
import lxml, lxml.etree
# from requests_toolbelt.utils import dump
import yaml
from orderedattrdict import AttrDict
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
import memoize
from memoize.core import *
import pytz
import dateutil.parser


CONFIG_DIR=os.path.expanduser("~/.mlb")
CONFIG_FILE=os.path.join(CONFIG_DIR, "config.yaml")
COOKIE_FILE=os.path.join(CONFIG_DIR, "cookies")


USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.12; rv:56.0) "
              "Gecko/20100101 Firefox/56.0.4")
PLATFORM = "macintosh"

BAM_SDK_VERSION="3.0"

API_KEY_URL= "https://www.mlb.com/tv/g490865/"
API_KEY_RE = re.compile(r'"apiKey":"([^"]+)"')
CLIENT_API_KEY_RE = re.compile(r'"clientApiKey":"([^"]+)"')

TOKEN_URL = (
    "https://media-entitlement.mlb.com/jwt"
    "?ipid={ipid}&fingerprint={fingerprint}==&os={platform}&appname=mlbtv_web"
)

GAME_CONTENT_URL="http://statsapi.mlb.com/api/v1/game/{game_id}/content"

# GAME_FEED_URL = "http://statsapi.mlb.com/api/v1/game/{game_id}/feed/live"

SCHEDULE_URL = "http://statsapi.mlb.com/api/v1/schedule?gamePk={game_id}"

ACCESS_TOKEN_URL = "https://edge.bamgrid.com/token"

STREAM_URL="https://edge.svcs.mlb.com/media/{media_id}/scenarios/browser"

store = {}
memo = Memoizer(store)

class MLBSession(object):

    HEADERS = {
        "User-agent": USER_AGENT
    }

    def __init__(self):

        self.load_config()
        self.session = requests.Session()
        self.session.cookies = LWPCookieJar(COOKIE_FILE)
        self.session.cookies.load(ignore_discard=True)
        self.session.headers = self.HEADERS
        self._token = None
        self._access_token = None

        # self.login()

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            raise Exception("config file %s not found" %(CONFIG_FILE))
        self.config = yaml.load(open(CONFIG_FILE), Loader=AttrDictYAMLLoader)

    def save_config(self):
        with open(CONFIG_FILE, 'w') as outfile:
            yaml.dump(self.config, outfile, default_flow_style=False)

    def login(self):

        initial_url = ("https://secure.mlb.com/enterworkflow.do"
                       "?flowId=registration.wizard&c_id=mlb")

        res = self.session.get(initial_url)
        if not res.status_code == 200:
            raise Exception(res.content)

        data = {
            "uri": "/account/login_register.jsp",
            "registrationAction": "identify",
            "emailAddress": self.config.userid,
            "password": self.config.password,
            "submitButton": ""
        }

        login_url = "https://securea.mlb.com/authenticate.do"

        res = self.session.post(
            login_url,
            data=data,
            headers={"Referer": (initial_url)}
        )

        if not self.ipid and self.fingerprint:
            raise Exception("Couldn't get ipid / fingerprint")

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

        if not "api_key" in self.config:
            self.update_api_keys()
        return self.config.api_key

    @property
    def client_api_key(self):

        if not "client_api_key" in self.config:
            self.update_api_keys()
        return self.config.client_api_key

    def update_api_keys(self):

        content = self.session.get("https://www.mlb.com/tv/g490865/").text
        parser = lxml.etree.HTMLParser()
        data = lxml.etree.parse(StringIO(content), parser)

        scripts = data.xpath(".//script")
        for script in scripts:
            if script.text and "apiKey" in script.text:
                self.config.api_key = API_KEY_RE.search(script.text).groups()[0]
            if script.text and "clientApiKey" in script.text:
                self.config.client_api_key = CLIENT_API_KEY_RE.search(script.text).groups()[0]

    @property
    @memo
    def token(self):
        # if self._token:
        #     return self._token
        headers = {"x-api-key": self.api_key}

        response = self.session.get(
            TOKEN_URL.format(
                ipid=self.ipid, fingerprint=self.fingerprint, platform=PLATFORM
            ),
            headers=headers
        )

        return response.content

    @property
    @memo
    def access_token(self):
        headers = {
            "Authorization": "Bearer %s" %(self.client_api_key),
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": BAM_SDK_VERSION,
            "x-bamsdk-platform": PLATFORM,
            "origin": "https://www.mlb.com"
        }

        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "platform": "browser",
            "setCookie": "false",
            "subject_token": self.token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt"
        }
        response = self._access_token = self.session.post(
            ACCESS_TOKEN_URL,
            data=data,
            headers=headers
        )
        token_response = response.json()

        return token_response["access_token"]
        # return self._access_token

    def content(self, game_id):

        return self.session.get(GAME_CONTENT_URL.format(game_id=game_id)).json()

    # def feed(self, game_id):

    #     return self.session.get(GAME_FEED_URL.format(game_id=game_id)).json()

    def schedule(self, game_id):

        return self.session.get(
            SCHEDULE_URL.format(game_id=game_id)
        ).json()

    def get_media(self, game_id, preferred_stream="HOME"):

        # print(j)
        for epg in self.content(game_id)["media"]["epg"]:
            if epg["title"] == "MLBTV":
                for item in epg["items"]:
                    if item["mediaFeedType"] == preferred_stream:
                        return item
                else:
                    return epg["items"][0]
                return epg

    def get_stream(self, game_id):
        # print(self.access_token)
        media = self.get_media(game_id)
        media_id = media["mediaId"]

        headers={
            "Authorization": self.access_token,
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": "3.0",
            "x-bamsdk-platform": PLATFORM,
            "origin": "https://www.mlb.com"
        }
        stream = self.session.get(
            STREAM_URL.format(media_id=media_id),
            headers=headers
        ).json()
        # raise Exception(stream)
        return stream

def play_stream(game_id, bandwidth, live_from_beginning=False):

    session = MLBSession()

    live = False
    offset = None

    stream = session.get_stream(game_id)
    url = stream["stream"]["complete"]

    media = session.get_media(game_id)
    media_id = media["mediaId"]
    media_state = media["mediaState"]

    if live_from_beginning and media_state == "MEDIA_ON": # live stream
        game = session.schedule(game_id)["dates"][0]["games"][0]
        start_time = dateutil.parser.parse(game["gameDate"])
        # print(start_time)
        # print(datetime.now(pytz.utc))
        # calculate HLS offset, which is negative from end of stream
        # for live streams
        offset =  datetime.now(pytz.utc) - (start_time.astimezone(pytz.utc))
        hours, remainder = divmod(offset.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        offset = "%d:%02d:%02d" %(hours, minutes, seconds)

    cmd = [
        "streamlink",
        # "-l", "debug",
        "--player", "mpv --osd-level=0 --no-osc --no-border",
        "--http-header",
        "Authorization=%s" %(session.access_token),
        url,
        bandwidth,
    ]
    if offset:
        cmd += ["--hls-start-offset", offset]
    logger.debug(" ".join(cmd))
    # if options.output_file:
    #     cmd += ["-o", options.output_file]


    # print(cmd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    proc.wait()
    session.save_config()

def main():

    global config

    parser = argparse.ArgumentParser()
    parser.add_argument("-b", "--beginning", help="play from beginning",
                        action="store_true")
    parser.add_argument("-r", "--resolution", help="stream resolution", default="720p")
    parser.add_argument("-o", "--output_file", help="save stream to file")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument('game_id', metavar='n',
                        type=int,
                        help='MLB game ID to stream')
    options, args = parser.parse_known_args()

    play_stream(options.game_id, options.resolution)


if __name__ == "__main__":
    main()
