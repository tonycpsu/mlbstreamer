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
from panwid.dialog import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader

from . import state
from .state import memo
from . import config
from . import play
from . import widgets
from .util import *
from .session import *



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

    def keypress(self, size, key):
        key = super(LineScoreDataTable, self).keypress(size, key)
        if key == "l":
            logger.debug("enable")
            self.line_score_table.enable_cell_selection()
        return key



class GamesDataTable(DataTable):

    columns = [
        DataTableColumn("start", width=6, align="right"),
        # DataTableColumn("game_type", label="type", width=5, align="right"),
        DataTableColumn("away", width=13),
        DataTableColumn("home", width=13),
        DataTableColumn("line"),
        # DataTableColumn("game_id", width=6, align="right"),
    ]


    def __init__(self, sport_id, game_date, game_type=None, *args, **kwargs):

        self.sport_id = sport_id
        self.game_date = game_date
        self.game_type = game_type

        self.line_score_table = None
        if not self.game_type:
            self.game_type = ""
        super(GamesDataTable, self).__init__(*args, **kwargs)

    def set_game_date(self, game_date):
        self.game_date = game_date
        self.reset()

    def query(self, *args, **kwargs):

        j = state.session.schedule(
            sport_id=self.sport_id,
            start=self.game_date,
            end=self.game_date,
            game_type=self.game_type
        )
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

                if "linescore" in g and len(g["linescore"]["innings"]):
                    self.line_score_table = LineScoreDataTable.from_mlb_api(
                            g["linescore"],
                            g["teams"]["away"]["team"]["abbreviation"],
                            g["teams"]["home"]["team"]["abbreviation"],
                            hide_spoilers
                    )
                    self.line_score = urwid.BoxAdapter(
                        self.line_score_table,
                        3
                    )
                else:
                    self.line_score = None
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
                    line = self.line_score
                )

class ResolutionDropdown(Dropdown):

    items = [
        ("720p", "720p_alt"),
        ("720p@30", "720p"),
        ("540p", "540p"),
        ("504p", "504p"),
        ("360p", "360p"),
        ("288p", "288p"),
        ("224p", "224p")
    ]

    label = "Resolution"

class Toolbar(urwid.WidgetWrap):

    def __init__(self):

        self.league_dropdown = Dropdown(AttrDict([
                ("MLB", 1),
                ("AAA", 11),
            ]) , label="League")

        self.live_stream_dropdown = Dropdown([
            "live",
            "from start"
        ], label="Live streams")

        self.resolution_dropdown = ResolutionDropdown(
            default=options.resolution
        )
        self.columns = urwid.Columns([
            ('weight', 1, self.league_dropdown),
            ('weight', 1, self.live_stream_dropdown),
            ('weight', 1, self.resolution_dropdown),
            # ("weight", 1, urwid.Padding(urwid.Text("")))
        ])
        self.filler = urwid.Filler(self.columns)
        super(Toolbar, self).__init__(self.filler)

    @property
    def sport_id(self):
        return (self.league_dropdown.selected_value)

    @property
    def resolution(self):
        return (self.resolution_dropdown.selected_value)

    @property
    def start_from_beginning(self):
        return self.live_stream_dropdown.selected_label == "from start"


class DateBar(urwid.WidgetWrap):

    def __init__(self, game_date):
        self.text = urwid.Text(game_date.strftime("%A, %Y-%m-%d"))
        self.fill = urwid.Filler(self.text)
        super(DateBar, self).__init__(self.fill)

    def set_date(self, game_date):
        self.text.set_text(game_date.strftime("%A, %Y-%m-%d"))


class WatchDialog(BasePopUp):

    signals = ["watch"]

    def __init__(self, game_id,
                 resolution=None, from_beginning=None):

        self.game_id = game_id
        self.resolution = resolution
        self.from_beginning = from_beginning

        self.game_data = state.session.schedule(
            game_id=self.game_id,
        )["dates"][0]["games"][0]
        # raise Exception(self.game_data)

        self.title = urwid.Text("%s@%s" %(
            self.game_data["teams"]["away"]["team"]["abbreviation"],
            self.game_data["teams"]["home"]["team"]["abbreviation"],
        ))

        feed_map = sorted([
            ("%s (%s)" %(e["mediaFeedType"].title(),
                         e["callLetters"]), e["mediaId"].lower())
            for e in state.session.get_media(self.game_id)
        ], key=lambda v: v[0])
        home_feed = next(state.session.get_media(
            self.game_id,
            preferred_stream = "home"
        ))
        self.feed_dropdown = Dropdown(
            feed_map,
            label="Feed",
            default=home_feed["mediaId"]
        )

        urwid.connect_signal(
            self.feed_dropdown,
            "change",
            lambda s, b, media_id: self.update_inning_dropdown(media_id)
        )

        self.resolution_dropdown = ResolutionDropdown(
            default=resolution
        )

        self.inning_dropdown_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.update_inning_dropdown(self.feed_dropdown.selected_value)

        self.ok_button = urwid.Button("OK")
        urwid.connect_signal(self.ok_button, "click", self.watch)

        self.cancel_button = urwid.Button("Cancel")
        urwid.connect_signal(
            self.cancel_button, "click",
            lambda b: urwid.signals.emit_signal(self, "close_popup")
        )

        pile = urwid.Pile([
            ("pack", self.title),
            ("weight", 1, urwid.Pile([
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                        ("weight", 1, self.feed_dropdown),
                        ("weight", 1, self.resolution_dropdown),
                    ]))),
                ("weight", 1, urwid.Filler(self.inning_dropdown_placeholder)),
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                    ("weight", 1, self.ok_button),
                    ("weight", 1, self.cancel_button),
                ])))
            ]))
        ])
        super(WatchDialog, self).__init__(pile)

    def update_inning_dropdown(self, media_id):
        # raise Exception(media_id)
        self.timestamps = state.session.media_timestamps(
            self.game_id, media_id
        )
        del self.timestamps["S"]
        timestamp_map = AttrDict(
            ( k if k[0] in "TB" else "Start", k ) for k in self.timestamps.keys()
        )
        timestamp_map["Live"] = False
        self.inning_dropdown = Dropdown(
            timestamp_map, label="Begin playback",
            default = timestamp_map["Start"] if self.from_beginning else timestamp_map["Live"]
        )
        self.inning_dropdown_placeholder.original_widget = self.inning_dropdown


    def watch(self, source):
        urwid.signals.emit_signal(
            self,
            "watch",
            self.game_id,
            self.resolution_dropdown.selected_value,
            self.feed_dropdown.selected_value,
            self.inning_dropdown.selected_value
        )
        urwid.signals.emit_signal(self, "close_popup")

    def keypress(self, size, key):

        if key == "meta enter":
            self.ok_button.keypress(size, "enter")
        else:
            # return super(WatchDialog, self).keypress(size, key)
            key = super(WatchDialog, self).keypress(size, key)
        if key:
            return
        return key


class ScheduleView(BaseView):

    def __init__(self, date):

        self.game_date = date
        self.toolbar = Toolbar()
        self.datebar = DateBar(self.game_date)
        self.table = GamesDataTable(self.toolbar.sport_id, self.game_date) # preseason
        urwid.connect_signal(self.table, "select",
                             lambda source, selection: self.open_watch_dialog(selection["game_id"]))
        self.pile  = urwid.Pile([
            (1, self.toolbar),
            (1, self.datebar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 2
        super(ScheduleView, self).__init__(self.pile)

    def open_watch_dialog(self, game_id):
        dialog = WatchDialog(game_id,
                             resolution = self.toolbar.resolution,
                             from_beginning = self.toolbar.start_from_beginning
        )
        urwid.connect_signal(
            dialog,
            "watch",
            self.watch
        )
        self.open_popup(dialog, width=30, height=20)

    def keypress(self, size, key):

        key = super(ScheduleView, self).keypress(size, key)
        if key in ["left", "right"]:
            self.game_date += timedelta(days= -1 if key == "left" else 1)
            self.datebar.set_date(self.game_date)
            self.table.set_game_date(self.game_date)
        elif key == "t":
            self.game_date = datetime.now().date()
            self.datebar.set_date(self.game_date)
            self.table.set_game_date(self.game_date)
        elif key == "w": # watch home stream
            self.watch(self.table.selection.data.game_id, preferred_stream="home")
        elif key == "W": # watch away stream
            self.watch(self.table.selection.data.game_id, preferred_stream="away")
        else:
            return key

    def watch(self, game_id,
              resolution=None, feed=None,
              offset=None, preferred_stream=None):

        try:
            state.proc = play.play_stream(
                game_id,
                resolution,
                call_letters = feed,
                preferred_stream = preferred_stream,
                offset = offset
            )
        except play.MLBPlayException as e:
            logger.error(e)



def main():

    global options
    global logger

    today = datetime.now(pytz.timezone('US/Eastern')).date()

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--date", help="game date",
                        type=valid_date,
                        default=today)
    parser.add_argument("-r", "--resolution", help="stream resolution",
                        default="720p_alt")
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

    state.session = MLBSession.new()

    entries = Dropdown.get_palette_entries()
    entries.update(ScrollingListBox.get_palette_entries())
    entries.update(DataTable.get_palette_entries())
    # raise Exception(entries)
    palette = Palette("default", **entries)
    screen = urwid.raw_display.Screen()
    screen.set_terminal_properties(256)

    view = ScheduleView(options.date)

    log_console = widgets.ConsoleWindow()
    # log_box = urwid.BoxAdapter(urwid.LineBox(log_console), 10)
    pile = urwid.Pile([
        ("weight", 1, urwid.LineBox(view)),
        (6, urwid.LineBox(log_console))
    ])

    def global_input(key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()
        else:
            return False

    state.loop = urwid.MainLoop(
        pile,
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
