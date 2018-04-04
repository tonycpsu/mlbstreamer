import sys
import logging
logger = logging.getLogger(__name__)

import os
import pytz
import subprocess
import argparse
from datetime import datetime, timedelta
import pytz
import shlex

import dateutil.parser
from orderedattrdict import AttrDict

from . import config
from . import state
from .session import *

class MLBPlayException(Exception):
    pass

class MLBPlayInvalidArgumentError(MLBPlayException):
    pass


def play_stream(game_specifier, resolution,
                offset_from_beginning=None,
                preferred_stream=None,
                output=None,
                date_json=None):

    live = False
    offset = None
    team = None
    game_number = 1

    if isinstance(game_specifier, int):
        game_id = game_specifier
        schedule = state.session.schedule(
            game_id = game_id
        )

    else:
        try:
            (game_date, team, game_number) = game_specifier
        except ValueError:
            (game_date, team) = game_specifier

        season = game_date.year
        teams_url = (
            "http://statsapi.mlb.com/api/v1/teams"
            "?sportId={sport}&season={season}".format(
                sport=1,
                season=season
            )
        )
        teams = AttrDict(
            (team["fileCode"], team["id"])
            for team in sorted(state.session.get(teams_url).json()["teams"],
                               key=lambda t: t["fileCode"])
        )

        if team not in teams:
            msg = "'%s' not a valid team code, must be one of:\n%s" %(
                game_specifier, " ".join(teams)
            )
            raise argparse.ArgumentTypeError(msg)

        schedule = state.session.schedule(
            start = game_date,
            end = game_date,
            sport_id = 1,
            team_id = teams[team]
        )

    try:
        date = schedule["dates"][-1]
        game = date["games"][game_number-1]
        game_id = game["gamePk"]
    except IndexError:
        raise MLBPlayException("No game %d found for %s on %s" %(
            game_number, team, game_date)
        )

    preferred_stream = (
        "HOME"
        if team == game["teams"]["home"]["team"]["fileCode"]
        else "AWAY"
    )

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
            # calculate HLS offset, which is negative from end of stream
            # for live streams
            start_time = dateutil.parser.parse(game["gameDate"])
            offset =  datetime.now(pytz.utc) - (start_time.astimezone(pytz.utc))
            offset += timedelta(minutes=-(offset_from_beginning))
            hours, remainder = divmod(offset.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            offset = "%d:%02d:%02d" %(hours, minutes, seconds)
        else:
            td = timedelta(minutes=offset_from_beginning)
            offset = str(td)
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
    if config.settings.streamlink_args:
        cmd += shlex.split(config.settings.streamlink_args)

    if offset:
        cmd += ["--hls-start-offset", offset]

    if output is not None:
        if output == True or os.path.isdir(output):
            outfile = get_output_filename(
                game,
                media["callLetters"],
                resolution
            )
            if os.path.isdir(output):
                outfile = os.path.join(output, outfile)
        else:
            outfile = output

        cmd += ["-o", outfile]

    logger.debug("Running cmd: %s" % " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    return proc

# def get_date_json(game_id, date_json):
#     if date_json is not None:
#         return date_json
#     return state.session.schedule(game_id=game_id)["dates"][-1]

def get_output_filename(game, station, resolution):
    try:
        # if (date is None):
        #     date = state.session.schedule(game_id=game["gamePk"])["dates"][-1]
        # game = date["games"][0]
        # Return file name in the format mlb.yyyy-mm-dd.away.vs.home.hh:mm.STATION.ts

        start_time = dateutil.parser.parse(
            game["gameDate"]
        ).astimezone(pytz.timezone("US/Eastern"))

        game_date = start_time.date().strftime("%Y%m%d")
        game_time = start_time.time().strftime("%H%M")
        return "mlb.%s.%s@%s.%s.%s.ts" \
               % (game_date,
                  game["teams"]["away"]["team"]["fileCode"],
                  game["teams"]["home"]["team"]["fileCode"],
                  game_time,
                  station.lower()
                  )
    except KeyError:
        return "mlb.%d.%s.ts" % (game["gamePk"], resolution)

def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)

def main():

    today = datetime.now(pytz.timezone('US/Eastern')).date()

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--date", help="game date",
                        type=valid_date,
                        default=today)
    parser.add_argument("-g", "--game-number",
                        help="number of team game on date (for doubleheaders)",
                        default=1,
                        type=int)
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

    global logger
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
        game_specifier = int(options.game)
    else:
        game_specifier = (options.date, options.game, options.game_number)

    try:
        proc = play_stream(
            game_specifier,
            options.resolution,
            offset_from_beginning = options.beginning,
            preferred_stream = preferred_stream,
            output = options.save_stream,
        )
        proc.wait()
    except MLBPlayInvalidArgumentError as e:
        raise argparse.ArgumentTypeError(str(e))
    except MLBPlayException as e:
        logger.error(e)


if __name__ == "__main__":
    main()
