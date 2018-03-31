import sys
import logging
logger = logging.getLogger(__name__)

import os
import pytz
import subprocess
import argparse
from datetime import datetime, timedelta

import dateutil.parser

from . import config
from . import state
from .session import *

class MLBPlayException(Exception):
    pass

def play_stream(game_id, resolution,
                offset_from_beginning=None,
                output=None):

    live = False
    offset = None

    try:
        media = next(state.session.get_media(game_id))
    except StopIteration:
        raise MLBPlayException("no matching media for game %d" %(game_id))

    media_id = media["mediaId"]
    media_state = media["mediaState"]

    stream = state.session.get_stream(media_id)

    try:
        media_url = stream["stream"]["complete"]
    except TypeError:
        raise MLBPlayException("no stream URL for game %d" %(game_id))


    if (offset_from_beginning is not None
        and media_state == "MEDIA_ON"): # live stream
        # game = state.session.schedule(game_id)["dates"][0]["games"][0]
        game = state.session.schedule(game_id=game_id)["dates"][0]["games"][0]
        start_time = dateutil.parser.parse(game["gameDate"])
        # calculate HLS offset, which is negative from end of stream
        # for live streams
        offset =  datetime.now(pytz.utc) - (start_time.astimezone(pytz.utc))
        offset += timedelta(minutes=-(offset_from_beginning))
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

    if output is not None:
        if output == True:
            outfile = "mlb.%d.%s.mp4" %(game_id, resolution)
        else:
            outfile = output
        cmd += ["-o", outfile]

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
                        nargs="?", metavar="offset_from_game_start",
                        type=int,
                        const=-10)
    parser.add_argument("-r", "--resolution", help="stream resolution",
                        default="720p")
    parser.add_argument("-s", "--save-stream", help="save stream to file",
                        nargs="?", const=True)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose logging")
    parser.add_argument("--init-config", help="initialize configuration",
                        action="store_true")
    parser.add_argument("game", metavar="game",
                        nargs="?",
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

    if options.init_config:
        config.settings.init_config()
        sys.exit(0)
    config.settings.load()

    if not options.game:
        parser.error("option game")

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

        if options.game not in teams:
            msg = "'%s' not a valid team code, must be one of:\n%s" % (options.game, " ".join(teams))
            raise argparse.ArgumentTypeError(msg)

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
            offset_from_beginning = options.beginning,
            output=options.save_stream
        )
        proc.wait()
    except MLBPlayException as e:
        logger.error(e)


if __name__ == "__main__":
    main()
