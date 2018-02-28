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

def play_stream(game_id, resolution, live_from_beginning=False):

    live = False
    offset = None

    stream = state.session.get_stream(game_id)
    url = stream["stream"]["complete"]

    media = state.session.get_media(game_id)
    media_id = media["mediaId"]
    media_state = media["mediaState"]

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
        url,
        resolution,
    ]
    if offset:
        cmd += ["--hls-start-offset", offset]
    logger.debug(" ".join(cmd))
    # if options.output_file:
    #     cmd += ["-o", options.output_file]


    # print(cmd)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    return proc

def main():


    parser = argparse.ArgumentParser()
    parser.add_argument("-b", "--beginning", help="play from beginning",
                        action="store_true")
    parser.add_argument("-r", "--resolution", help="stream resolution", default="720p")
    parser.add_argument("-o", "--output_file", help="save stream to file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose logging")
    parser.add_argument('game_id', metavar='n',
                        type=int,
                        help='MLB game ID to stream')
    options, args = parser.parse_known_args()

    if options.verbose:
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s [%(levelname)8s] %(message)s",
                                      datefmt='%Y-%m-%d %H:%M:%S')
        fh = logging.FileHandler("dropdown.log")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    else:
        logger.addHandler(logging.NullHandler())

    config.settings.load()
    state.session = MLBSession.get()

    proc = play_stream(options.game_id, options.resolution)
    proc.wait()

if __name__ == "__main__":
    main()
