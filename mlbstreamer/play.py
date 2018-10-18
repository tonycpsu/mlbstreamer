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
from itertools import chain

import dateutil.parser
from orderedattrdict import AttrDict

from . import config
from . import state
from . import session
from . import utils
from .exceptions import *
# from .session import *


def handle_exception(exc_type, exc_value, exc_traceback):
    if state.session:
        state.session.save()
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

def play_stream(game_specifier, resolution=None,
                offset=None,
                media_id = None,
                preferred_stream=None,
                call_letters=None,
                output=None,
                verbose=0):

    live = False
    team = None
    game_number = 1
    game_date = None
    # sport_code = "mlb" # default sport is MLB

    # media_title = "MLBTV"
    media_id = None
    allow_stdout=False

    if resolution is None:
        resolution = "best"

    if isinstance(game_specifier, int):
        game_id = game_specifier
        schedule = state.session.schedule(
            game_id = game_id
        )

    else:
        try:
            (game_date, team, game_number) = game_specifier.split(".")
        except ValueError:
            try:
                (game_date, team) = game_specifier.split(".")
            except ValueError:
                game_date = datetime.now().date()
                team = game_specifier

        if "-" in team:
            (sport_code, team) = team.split("-")

        game_date = dateutil.parser.parse(game_date)
        game_number = int(game_number)
        teams =  state.session.teams(season=game_date.year)
        team_id = teams.get(team)

        if not team:
            msg = "'%s' not a valid team code, must be one of:\n%s" %(
                game_specifier, " ".join(teams)
            )
            raise argparse.ArgumentTypeError(msg)

        schedule = state.session.schedule(
            start = game_date,
            end = game_date,
            # sport_id = sport["id"],
            team_id = team_id
        )
        # raise Exception(schedule)


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

    away_team_abbrev = game["teams"]["away"]["team"]["abbreviation"].lower()
    home_team_abbrev = game["teams"]["home"]["team"]["abbreviation"].lower()

    if not preferred_stream or call_letters:
        preferred_stream = (
            "away"
            if team == away_team_abbrev
            else "home"
        )

    try:
        media = next(state.session.get_media(
            game_id,
            media_id = media_id,
            # title=media_title,
            preferred_stream=preferred_stream,
            call_letters = call_letters
        ))
    except StopIteration:
        raise MLBPlayException("no matching media for game %d" %(game_id))

    # media_id = media["mediaId"] if "mediaId" in media else media["guid"]

    media_state = media["mediaState"]

    # Get any team-specific profile overrides, and apply settings for them
    profiles = tuple([ list(d.values())[0]
                 for d in config.settings.profile_map.get("team", {})
                 if list(d.keys())[0] in [
                         away_team_abbrev, home_team_abbrev
                 ] ])

    if len(profiles):
        # override proxies for team, if defined
        if len(config.settings.profiles[profiles].proxies):
            old_proxies = state.session.proxies
            state.session.proxies = config.settings.profiles[profiles].proxies
            state.session.refresh_access_token(clear_token=True)
            state.session.proxies = old_proxies

    if "playbacks" in media:
        playback = media["playbacks"][0]
        media_url = playback["location"]
    else:
        stream = state.session.get_stream(media)

        try:
            # media_url = stream["stream"]["complete"]
            media_url = stream.url
        except (TypeError, AttributeError):
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

    header_args = []
    cookie_args = []

    if state.session.headers:
        header_args = list(
            chain.from_iterable([
                ("--http-header", f"{k}={v}")
            for k, v in state.session.headers.items()
        ]))

    if state.session.cookies:
        cookie_args = list(
            chain.from_iterable([
                ("--http-cookie", f"{c.name}={c.value}")
            for c in state.session.cookies
        ]))

    cmd = [
        "streamlink",
        # "-l", "debug",
        "--player", config.settings.profile.player,
    ] + cookie_args + header_args + [
        media_url,
        resolution,
    ]

    if config.settings.profile.streamlink_args:
        cmd += shlex.split(config.settings.profile.streamlink_args)

    if offset_timestamp:
        cmd += ["--hls-start-offset", offset_timestamp]

    if verbose > 1:

        allow_stdout=True
        cmd += ["-l", "debug"]

        if verbose > 2:
            if not output:
                cmd += ["-v"]
            cmd += ["--ffmpeg-verbose"]

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
    proc = subprocess.Popen(cmd, stdout=None if allow_stdout else open(os.devnull, 'w'))
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

    init_parser = argparse.ArgumentParser(add_help=False)
    init_parser.add_argument("--init-config", help="initialize configuration",
                        action="store_true")
    init_parser.add_argument("-p", "--profile", help="use alternate config profile")
    options, args = init_parser.parse_known_args()

    if options.init_config:
        config.settings.init_config()
        sys.exit(0)

    config.settings.load()

    if options.profile:
        config.settings.set_profile(options.profile)

    parser = argparse.ArgumentParser(
        description=init_parser.format_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("-b", "--begin",
                        help="begin playback at this offset from start",
                        nargs="?", metavar="offset_from_game_start",
                        type=begin_arg_to_offset,
                        const=0)
    parser.add_argument("-r", "--resolution", help="stream resolution",
                        default=config.settings.profile.default_resolution)
    parser.add_argument("-s", "--save-stream", help="save stream to file",
                        nargs="?", const=True)
    parser.add_argument("--no-cache", help="do not use response cache",
                        action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    parser.add_argument("game", metavar="game",
                        nargs="?",
                        help="team abbreviation or MLB game ID")
    options, args = parser.parse_known_args(args)

    try:
        (provider, game) = options.game.split("/", 1)
    except ValueError:
        game = options.game#.split(".", 1)[1]
        provider = list(config.settings.profile.providers.keys())[0]

    if game.isdigit():
        game_specifier = int(game)
    else:
        game_specifier = game

    utils.setup_logging(options.verbose - options.quiet)

    if not options.game:
        parser.error("option game")

    state.session = session.new(provider)
    preferred_stream = None
    date = None

    try:
        proc = play_stream(
            game_specifier,
            options.resolution,
            offset = options.begin,
            preferred_stream = preferred_stream,
            output = options.save_stream,
            verbose = options.verbose
        )
        proc.wait()
    except MLBPlayInvalidArgumentError as e:
        raise argparse.ArgumentTypeError(str(e))
    except MLBPlayException as e:
        logger.error(e)


if __name__ == "__main__":
    main()
