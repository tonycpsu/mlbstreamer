import logging
import sys
import argparse
from datetime import datetime
from orderedattrdict import AttrDict

LOG_LEVEL_DEFAULT=3
LOG_LEVELS = [
    "critical",
    "error",
    "warning",
    "info",
    "debug",
    "trace"
]
def setup_logging(level=0, handlers=[], quiet_stdout=False):

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

    if not isinstance(handlers, list):
        handlers = [handlers]

    logger = logging.getLogger()
    formatter = logging.Formatter(
        "%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logger.setLevel(level)
    outh = logging.StreamHandler(sys.stdout)
    outh.setLevel(logging.ERROR if quiet_stdout else level)

    handlers.insert(0, outh)
    # if not handlers:
    #     handlers = [logging.StreamHandler(sys.stdout)]
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # logger = logging.basicConfig(
    #     level=level,
    #     format="%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s",
    #     datefmt="%Y-%m-%d %H:%M:%S"
    # )

    logging.getLogger("requests").setLevel(level+1)
    logging.getLogger("urllib3").setLevel(level+1)


def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)
