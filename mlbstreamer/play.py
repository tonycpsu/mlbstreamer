import sys
import logging
logger = logging.getLogger(__name__)

import os
# import sys
# import re
import subprocess
import argparse
from datetime import datetime

import dateutil.parser

from . import config
from . import state
from .session import *

class MLBPlayException(Exception):
    pass

def play_stream(game_id, resolution, live_from_beginning=False):

    live = False
    offset = None

    # stream = state.session.get_stream(game_id)
    # if not stream:
    #     raise MLBPlayException("no matching media for game %d" %(game_id))
    # logger.debug(stream)
    # url = stream["stream"]["complete"]

    try:
        media = next(state.session.get_media(game_id))
    except StopIteration:
        raise MLBPlayException("no matching media for game %d" %(game_id))


    media_id = media["mediaId"]
    media_state = media["mediaState"]

    stream = state.session.get_stream(media_id)
    media_url = stream["stream"]["complete"]

    if live_from_beginning and media_state == "MEDIA_ON": # live stream
        game = state.session.schedule(game_id)["dates"][0]["games"][0]
        start_time = dateutil.parser.parse(game["gameDate"])
        # print(start_time)
        # print(datetime.now(pytz.utc))
        # calculate HLS offset, which is negative from end of stream
        # for live streams
        offset =  datetime.now(pytz.utc) - (start_time.astimezone(pytz.utc))
        hours, remainder = divmod(offset.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        offset = "%d:%02d:%02d" %(hours, minutes, seconds)
        logger.info("starting at time offset %s" %(offset))

    cmd = [
        "streamlink",
        # "-l", "debug",
        "--player", config.settings.player,
        "--http-header",
        "Authorization=%s" %(state.session.access_token),
        media_url,
        resolution,
    ]
    if offset:
        cmd += ["--hls-start-offset", offset]
    logger.debug(" ".join(cmd))
    # if options.output_file:
    #     cmd += ["-o", options.output_file]


    logger.debug(cmd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    return proc

def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)

def main():

    today = datetime.now().date()

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--date", help="game date",
                        type=valid_date,
                        default=today)
    parser.add_argument("-b", "--beginning",
                        help="play live streams from beginning",
                        action="store_true")
    parser.add_argument("-r", "--resolution", help="stream resolution", default="720p")
    parser.add_argument("-o", "--output_file", help="save stream to file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose logging")
    parser.add_argument('game', metavar='n',
                        help="team abbreviation or MLB game ID")
    options, args = parser.parse_known_args()

    logger = logging.getLogger("mlbstreamer")
    if options.verbose:
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s [%(levelname)8s] %(message)s",
                                      datefmt='%Y-%m-%d %H:%M:%S')
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        logger.addHandler(logging.NullHandler())

    config.settings.load()
    state.session = MLBSession.new()

    if options.game.isdigit():
        game_id = int(options.game)
    else:

        season = today.year
        teams_url = (
            "http://statsapi.mlb.com/api/v1/teams"
            "?sportId={sport}&season={season}".format(
                sport=1,
                season=season
            )
        )
        teams = {
            team["fileCode"]: team["id"]
            for team in state.session.get(teams_url).json()["teams"]
        }
        schedule = state.session.schedule(
            start = options.date,
            end = options.date,
            sport_id = 1,
            team_id = teams[options.game]
        )
        game_id = schedule["dates"][0]["games"][0]["gamePk"]

    try:
        proc = play_stream(
            game_id,
            options.resolution,
            live_from_beginning = options.beginning)
        proc.wait()
    except MLBPlayException as e:
        logger.error(e)


if __name__ == "__main__":
    main()
