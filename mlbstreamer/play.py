import sys
import logging
logger = logging.getLogger(__name__)

import os
import pytz
import subprocess
import argparse
from datetime import datetime, timedelta
import pytz

import dateutil.parser

from . import config
from . import state
from .session import *

class MLBPlayException(Exception):
    pass

def play_stream(game_id, resolution,
                offset_from_beginning=None,
                preferred_stream=None,
                output=None,
                date_json=None):

    live = False
    offset = None

    try:
        media = next(state.session.get_media(game_id,
                                             preferred_stream=preferred_stream))
    except StopIteration:
        raise MLBPlayException("no matching media for game %d" %(game_id))

    media_id = media["mediaId"]
    media_state = media["mediaState"]

    stream = state.session.get_stream(media_id)

    try:
        media_url = stream["stream"]["complete"]
    except TypeError:
        raise MLBPlayException("no stream URL for game %d" %(game_id))


    if (offset_from_beginning is not None):
        if (media_state == "MEDIA_ON"): # live stream
            game = get_date_json(game_id, date_json)["games"][0]
            start_time = dateutil.parser.parse(game["gameDate"])
            # calculate HLS offset, which is negative from end of stream
            # for live streams
            offset =  datetime.now(pytz.utc) - (start_time.astimezone(pytz.utc))
            offset += timedelta(minutes=-(offset_from_beginning))
            hours, remainder = divmod(offset.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            offset = "%d:%02d:%02d" %(hours, minutes, seconds)
        else:
            offset = "0:%02d:00" %(offset_from_beginning)
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
        if output == True or os.path.isdir(output):
            outfile = get_output_filename(game_id, media, date_json, resolution)
            if os.path.isdir(output):
                outfile = os.path.join(output, outfile)
        else:
            outfile = output

        cmd += ["-o", outfile]

    logger.debug(cmd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    return proc

def get_date_json(game_id, date_json):
    if date_json is not None:
        return date_json
    return state.session.schedule(game_id=game_id)["dates"][-1]

def get_output_filename(game_id, media, date, resolution):
    try:
        if (date is None):
            date = state.session.schedule(game_id=game_id)["dates"][-1]
        game = date["games"][0]
        # Return file name in the format yyyy-mm-dd.away.vs.home-STATION-mlb.ts
        return "%s.%s.vs.%s-%s-mlb.ts" \
               % (date["date"],
                  game["teams"]["away"]["team"]["fileCode"],
                  game["teams"]["home"]["team"]["fileCode"],
                  media["callLetters"])
    except KeyError:
        return "mlb.%d.%s.ts" % (game_id, resolution)

def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)

def main():

    today = datetime.now(pytz.timezone('US/Eastern')).date()

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

    preferred_stream = None
    date = None

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
            msg = "'%s' not a valid team code, must be one of:\n%s" %(
                options.game, " ".join(teams)
            )
            raise argparse.ArgumentTypeError(msg)

        schedule = state.session.schedule(
            start = options.date,
            end = options.date,
            sport_id = 1,
            team_id = teams[options.game]
        )
        date = schedule["dates"][-1]
        game = date["games"][0]
        game_id = game["gamePk"]
        preferred_stream = (
            "HOME"
            if options.game == game["teams"]["home"]["team"]["fileCode"]
            else "AWAY"
        )

    try:
        proc = play_stream(
            game_id,
            options.resolution,
            offset_from_beginning = options.beginning,
            preferred_stream = preferred_stream,
            output = options.save_stream,
            date_json= date
        )
        proc.wait()
    except MLBPlayException as e:
        logger.error(e)


if __name__ == "__main__":
    main()
