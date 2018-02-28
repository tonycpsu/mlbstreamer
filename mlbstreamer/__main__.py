import logging
# global logger
# logger = logging.getLogger(__name__)
import os
from datetime import datetime, timedelta
from collections import namedtuple
import argparse
import subprocess
import select

import urwid
import urwid.raw_display
from urwid_utils.palette import *
from panwid.datatable import *
from panwid.listbox import ScrollingListBox
from panwid.dropdown import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader

from . import state
from . import config
from . import play
from . import widgets
from .session import *


SCHEDULE_TEMPLATE=(
    "http://statsapi.mlb.com/api/v1/schedule"
    "?sportId={sport_id}&startDate={start}&endDate={end}&gameType={game_type}"
    "&hydrate=linescore,team"
)


class UrwidLoggingHandler(logging.Handler):

    # def __init__(self, console):

    #     self.console = console
    #     super(UrwidLoggingHandler, self).__init__()

    def connect(self, pipe):
        self.pipe = pipe

    def emit(self, rec):

        msg = self.format(rec)
        (ignore, ready, ignore) = select.select([], [self.pipe], [])
        if self.pipe in ready:
            os.write(self.pipe, (msg+"\n").encode("utf-8"))


def parse_int(n):
    try:
        return int(n)
    except ValueError:
        return n
    except TypeError:
        return None

class LineScore(AttrDict):
    pass

class Side(AttrDict):
    pass

class Inning(AttrDict):
    pass


class LineScoreDataTable(DataTable):

    @classmethod
    def from_mlb_api(cls, line_score,
                     away_team=None, home_team=None,
                     hide_spoilers=False
    ):

        columns = [
            DataTableColumn("team", width=6, label="", align="right", padding=1),
        ]

        if "teams" in line_score:
            tk = line_score["teams"]
        else:
            tk = line_score

        data = []
        for s, side in enumerate(["away", "home"]):

            line = AttrDict()

            if isinstance(line_score["innings"], list):
                for i, inning in enumerate(line_score["innings"]):
                    if not s:
                        columns.append(
                            DataTableColumn(str(i+1), label=str(i+1), width=3)
                        )
                        line.team = away_team
                    else:
                        line.team = home_team

                    if hide_spoilers:
                        setattr(line, str(i+1), "?")

                    elif side in inning:
                        if isinstance(inning[side], dict) and "runs" in inning[side]:
                            setattr(line, str(i+1), parse_int(inning[side]["runs"]))
                        else:
                            if "runs" in inning[side]:
                                inning_score.append(parse_int(inning[side]))
                    else:
                        setattr(line, str(i+1), "X")

                for n in range(i+1, 9):
                    if not s:
                        columns.append(
                            DataTableColumn(str(n+1), label=str(n+1), width=3)
                        )
                    if hide_spoilers:
                        setattr(line, str(n+1), "?")

            if not s:
                columns.append(
                    DataTableColumn("empty", label="", width=3)
                )

            for stat in ["runs", "hits", "errors"]:
                if not stat in tk[side]: continue

                if not s:
                    columns.append(
                        DataTableColumn(stat, label=stat[0].upper(), width=3)
                    )
                if not hide_spoilers:
                    setattr(line, stat, parse_int(tk[side][stat]))
                else:
                    setattr(line, stat, "?")


            data.append(line)
        # raise Exception([c.name for c in columns])
        return cls(columns, data=data)



class GamesDataTable(DataTable):

    signals = ["watch"]

    columns = [
        DataTableColumn("start", width=6, align="right"),
        # DataTableColumn("game_type", label="type", width=5, align="right"),
        DataTableColumn("away", width=15),
        DataTableColumn("home", width=15),
        DataTableColumn("line"),
        # DataTableColumn("game_id", width=6, align="right"),
    ]


    def __init__(self, sport_id, game_date, game_type=None, *args, **kwargs):

        self.sport_id = sport_id
        self.game_date = game_date
        self.game_type = game_type
        if not self.game_type:
            self.game_type = ""
        super(GamesDataTable, self).__init__(*args, **kwargs)

    def keypress(self, size, key):

        key = super(GamesDataTable, self).keypress(size, key)
        if key in ["left", "right"]:
            self.game_date += timedelta(days= -1 if key == "left" else 1)
            self.reset()
        elif key == "t":
            self.game_date = datetime.now().date()
            self.reset()
        elif key == "w":
            self._emit("watch", self.selection.data.game_id)
        else:
            return key

    def query(self, *args, **kwargs):

        url = SCHEDULE_TEMPLATE.format(
            sport_id=self.sport_id,
            start=self.game_date.strftime("%Y-%m-%d"),
            end=self.game_date.strftime("%Y-%m-%d"),
            game_type=self.game_type
        )
        j = requests.get(url).json()

        for d in j["dates"]:

            for g in d["games"]:
                game_pk = g["gamePk"]
                game_type = g["gameType"]
                status = g["status"]["statusCode"]
                away_team = g["teams"]["away"]["team"]["teamName"]
                home_team = g["teams"]["home"]["team"]["teamName"]
                away_abbrev = g["teams"]["away"]["team"]["abbreviation"]
                home_abbrev = g["teams"]["home"]["team"]["abbreviation"]
                start_time = dateutil.parser.parse(g["gameDate"])
                if config.settings.time_zone:
                    start_time = start_time.astimezone(config.settings.tz)

                hide_spoilers = set([away_abbrev, home_abbrev]).intersection(
                    set(config.settings.get("hide_spoiler_teams", [])))

                if len(g["linescore"]["innings"]):
                    line_score = urwid.BoxAdapter(
                        LineScoreDataTable.from_mlb_api(
                            g["linescore"],
                            g["teams"]["away"]["team"]["abbreviation"],
                            g["teams"]["home"]["team"]["abbreviation"],
                            hide_spoilers
                        ),
                        3
                    )
                else:
                    line_score = None
                yield dict(
                    game_id = game_pk,
                    game_type = game_type,
                    away = away_team,
                    home = home_team,
                    start = "%d:%02d%s" %(
                        start_time.hour - 12 if start_time.hour > 12 else start_time.hour,
                        start_time.minute,
                        "p" if start_time.hour >= 12 else "a"
                    ),
                    line = line_score
                )


class Toolbar(urwid.WidgetWrap):

    def __init__(self):

        self.live_stream_dropdown = Dropdown([
            "from beginning",
            "live"
        ], label="Live streams: ")

        self.resolution_dropdown = Dropdown(AttrDict([
            ("720p (60fps)", "720p_alt"),
            ("720p", "720p"),
            ("540p", "540p"),
            ("504p", "504p"),
            ("360p", "360p"),
            ("288p", "288p"),
            ("224p", "224p")
        ]), label="resolution")

        self.columns = urwid.Columns([
            (36, self.live_stream_dropdown),
            (30, self.resolution_dropdown),
            ("weight", 1, urwid.Padding(urwid.Text("")))
        ])
        self.filler = urwid.Filler(self.columns)
        super(Toolbar, self).__init__(self.filler)

    @property
    def resolution(self):
        return (self.resolution_dropdown.selected_value)

    @property
    def start_from_beginning(self):
        return self.live_stream_dropdown.selected_label == "from beginning"

class ScheduleView(urwid.WidgetWrap):

    def __init__(self, sport_id):

        self.sport_id = sport_id

        today = datetime.now().date()
        self.table = GamesDataTable(sport_id, today) # preseason
        urwid.connect_signal(self.table, "watch",
                             lambda dsource, game_id: self.watch(game_id))
        self.toolbar = Toolbar()
        self.pile  = urwid.Pile([
            (1, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 1
        super(ScheduleView, self).__init__(self.pile)

    def watch(self, game_id):
        logger.info("playing game %d at %s" %(game_id, self.toolbar.resolution))
        play.play_stream(
            game_id,
            self.toolbar.resolution,
            self.toolbar.start_from_beginning,
        )


def main():

    global options

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    options, args = parser.parse_known_args()

    log_file = os.path.join(config.CONFIG_DIR, "mlbstreamer.log")

    formatter = logging.Formatter(
        "%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    logger = logging.getLogger("mlbstreamer")
    logger.setLevel(logging.INFO)
    logger.addHandler(fh)

    ulh = UrwidLoggingHandler()
    ulh.setLevel(logging.DEBUG)
    ulh.setFormatter(formatter)
    logger.addHandler(ulh)

    logger.debug("mlbstreamer starting")
    config.settings.load()

    state.session = MLBSession.get()

    entries = {
        "dropdown_text": PaletteEntry(
            foreground = "light gray",
            background = "dark blue",
            foreground_high = "light gray",
            background_high = "#003",
        ),
        "dropdown_focused": PaletteEntry(
            foreground = "white",
            background = "light blue",
            foreground_high = "white",
            background_high = "#009",
        ),
        "dropdown_highlight": PaletteEntry(
            foreground = "yellow",
            background = "light blue",
            foreground_high = "yellow",
            background_high = "#009",
        ),
        "dropdown_label": PaletteEntry(
            foreground = "white",
            background = "black"
        ),
        "dropdown_prompt": PaletteEntry(
            foreground = "light blue",
            background = "black"
        ),
    }

    entries = DataTable.get_palette_entries(user_entries=entries)
    # raise Exception(entries["dropdown_text"])
    palette = Palette("default", **entries)
    screen = urwid.raw_display.Screen()
    screen.set_terminal_properties(256)

    MLB_SPORT_ID=1 # MLB. http://statsapi.mlb.com/api/v1/sports/ for others
    view = ScheduleView(MLB_SPORT_ID)

    log_console = widgets.ConsoleWindow()
    log_box = urwid.BoxAdapter(urwid.LineBox(log_console), 10)
    frame = urwid.Frame(urwid.LineBox(view), footer=log_box)

    def global_input(key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()
        else:
            return False

    state.loop = urwid.MainLoop(
        frame,
        palette,
        screen=screen,
        unhandled_input=global_input,
        pop_ups=True
    )
    ulh.connect(state.loop.watch_pipe(log_console.log_message))
    logger.info("mlbstreamer starting")
    if options.verbose:
        logger.setLevel(logging.DEBUG)

    state.loop.run()


if __name__ == "__main__":
    main()
