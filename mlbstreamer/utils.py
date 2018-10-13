import logging
import argparse
from datetime import datetime
from orderedattrdict import AttrDict

MLB_HLS_RESOLUTION_MAP = AttrDict([
    ("720p", "720p_alt"),
    ("720p@30", "720p"),
    ("540p", "540p"),
    ("504p", "504p"),
    ("360p", "360p"),
    ("288p", "288p"),
    ("224p", "224p")
])

LOG_LEVEL_DEFAULT=3
LOG_LEVELS = [
    "critical",
    "error",
    "warning",
    "info",
    "debug",
    "trace"
]
def setup_logging(level=0):

    level = LOG_LEVEL_DEFAULT + level
    if level < 0 or level >= len(LOG_LEVELS):
        raise Exception("bad log level: %d" %(level))
    # add "trace" log level
    TRACE_LEVEL_NUM = 9
    logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")
    logging.TRACE = TRACE_LEVEL_NUM
    def trace(self, message, *args, **kws):
        if self.isEnabledFor(TRACE_LEVEL_NUM):
            self._log(TRACE_LEVEL_NUM, message, args, **kws)
    logging.Logger.trace = trace

    if isinstance(level, str):
        level = getattr(logging, level.upper())
    else:
        level = getattr(logging, LOG_LEVELS[level].upper())

    # logger = logging.getLogger()
    # formatter = logging.Formatter(
    #     "%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s",
    #     datefmt="%Y-%m-%d %H:%M:%S"
    # )
    # handler = logging.StreamHandler(sys.stdout)
    # handler.setFormatter(formatter)
    # logger.addHandler(handler)
    # logger.setLevel(level)

    logger = logging.basicConfig(
        level=level,
        format="%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logging.getLogger("requests").setLevel(level)
    logging.getLogger("urllib3").setLevel(level)


def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)
