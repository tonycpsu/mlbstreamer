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
from .util import *
from .session import *

class MLBPlayException(Exception):
    pass

class MLBPlayInvalidArgumentError(MLBPlayException):
    pass

def play_stream(game_specifier, resolution=None,
                offset=None,
                media_id = None,
                preferred_stream=None,
                call_letters=None,
                output=None):

    live = False
    team = None
    game_number = 1
    sport_code = "mlb" # default sport is MLB

    media_title = "MLBTV"
    media_id = None

    if resolution is None:
        resolution = "best"

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

        if "/" in team:
            (sport_code, team) = team.split("/")


        if sport_code != "mlb":
            media_title = "MiLBTV"
            raise MLBPlayException("Sorry, MiLB.tv streams are not yet supported")

        sports_url = (
            "http://statsapi.mlb.com/api/v1/sports"
        )
        with state.session.cache_responses_long():
            sports = state.session.get(sports_url).json()

        sport = next(s for s in sports["sports"] if s["code"] == sport_code)

        season = game_date.year
        teams_url = (
            "http://statsapi.mlb.com/api/v1/teams"
            "?sportId={sport}&season={season}".format(
                sport=sport["id"],
                season=season
            )
        )

        with state.session.cache_responses_long():
            teams = AttrDict(
                (team["abbreviation"].lower(), team["id"])
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
            sport_id = sport["id"],
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

    logger.info("playing game %d at %s" %(
        game_id, resolution)
    )

    if not preferred_stream or call_letters:
        preferred_stream = (
            "away"
            if team == game["teams"]["away"]["team"]["abbreviation"].lower()
            else "home"
        )

    try:
        media = next(state.session.get_media(
            game_id,
            media_id = media_id,
            title=media_title,
            preferred_stream=preferred_stream,
            call_letters = call_letters
        ))
    except StopIteration:
        raise MLBPlayException("no matching media for game %d" %(game_id))

    media_id = media["mediaId"] if "mediaId" in media else media["guid"]

    media_state = media["mediaState"]

    if "playbacks" in media:
        playback = media["playbacks"][0]
        media_url = playback["location"]
    else:
        stream = state.session.get_stream(media_id)

        try:
            media_url = stream["stream"]["complete"]
        except TypeError:
            raise MLBPlayException("no stream URL for game %d" %(game_id))

    offset_timestamp = None
    offset_seconds = None

    if (offset is not False and offset is not None):

        timestamps = state.session.media_timestamps(game_id, media_id)

        if isinstance(offset, str):
            if not offset in timestamps:
                raise MLBPlayException("Couldn't find inning %s" %(offset))
            offset = timestamps[offset] - timestamps["SO"]
            logger.debug("inning offset: %s" %(offset))

        if (media_state == "MEDIA_ON"): # live stream
            logger.debug("live stream")
            # calculate HLS offset, which is negative from end of stream
            # for live streams
            start_time = dateutil.parser.parse(timestamps["S"])
            offset_delta = (
                datetime.now(pytz.utc)
                - start_time.astimezone(pytz.utc)
                + (timedelta(seconds=-offset))
            )
        else:
            logger.debug("recorded stream")
            offset_delta = timedelta(seconds=offset)

        offset_seconds = offset_delta.seconds
        offset_timestamp = str(offset_delta)
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

    if offset_timestamp:
        cmd += ["--hls-start-offset", offset_timestamp]

    if output is not None:
        if output == True or os.path.isdir(output):
            outfile = get_output_filename(
                game,
                media["callLetters"],
                resolution,
                offset=str(offset_seconds)
            )
            if os.path.isdir(output):
                outfile = os.path.join(output, outfile)
        else:
            outfile = output

        cmd += ["-o", outfile]

    logger.debug("Running cmd: %s" % " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    return proc


def get_output_filename(game, station, resolution, offset=None):
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
        if offset:
            game_time = "%s_%s" %(game_time, offset)
        return "mlb.%s.%s@%s.%s.%s.ts" \
               % (game_date,
                  game["teams"]["away"]["team"]["fileCode"],
                  game["teams"]["home"]["team"]["fileCode"],
                  game_time,
                  station.lower()
                  )
    except KeyError:
        return "mlb.%d.%s.ts" % (game["gamePk"], resolution)


def begin_arg_to_offset(value):
    if value.isdigit():
        # Integer number of seconds
        value = int(value)
    else:
        try:
            value = (
                datetime.strptime(value, "%H:%M:%S")
                - datetime.min
            ).seconds
        except ValueError:
            try:
                value = (
                    datetime.strptime(value, "%M:%S")
                    - datetime.min
                ).seconds
            except:
                if not (value == "S"
                        or (value[0] in "TB" and value[1:].isdigit())
                ):
                    raise argparse.ArgumentTypeError(
                        "Offset must be an integer number of seconds, "
                        "a time string e.g. 1:23:45, "
                        "or a string like T1 or B3 to select a half inning"
                    )
    return value


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
    parser.add_argument("-b", "--begin",
                        help="begin playback at this offset from start",
                        nargs="?", metavar="offset_from_game_start",
                        type=begin_arg_to_offset,
                        const=0)
    parser.add_argument("-r", "--resolution", help="stream resolution",
                        default="720p")
    parser.add_argument("-s", "--save-stream", help="save stream to file",
                        nargs="?", const=True)
    parser.add_argument("--no-cache", help="do not use response cache",
                        action="store_true")
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

    state.session = MLBSession.new(no_cache=options.no_cache)

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
            offset = options.begin,
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
