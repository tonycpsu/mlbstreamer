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

def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)
