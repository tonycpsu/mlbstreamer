import logging
logger = logging.getLogger(__name__)
import os
from datetime import datetime, timedelta
from collections import namedtuple
import argparse
import subprocess

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

CONFIG_DIR=os.path.expanduser("~/.mlb")
CONFIG_FILE=os.path.join(CONFIG_DIR, "config.yaml")

from . import play

def load_config():
    global config
    if not os.path.exists(CONFIG_FILE):
        raise Exception("config file %s not found" %(CONFIG_FILE))
    config = yaml.load(open(CONFIG_FILE), Loader=AttrDictYAMLLoader)
    if config.time_zone:
        config.tz = pytz.timezone(config.time_zone)


def save_config():
    global config
    with open(CONFIG_FILE, 'w') as outfile:
        yaml.dump(config, outfile)

SCHEDULE_TEMPLATE=(
    "http://statsapi.mlb.com/api/v1/schedule"
    "?sportId={sport_id}&startDate={start}&endDate={end}&gameType={game_type}"
    "&hydrate=linescore,team"
)


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
                if config.time_zone:
                    # print(start_time)
                    start_time = start_time.astimezone(config.tz)
                    # print(start_time)
                # if status == "DI":
                #     logger.info("game %s (%s) postponed" %(game_pk, start_time))
                #     continue
                # game_num = g["gameNumber"]
                # game = ProGame_MLB.get_for_update(mlb_game_id = game_pk)
                # if game:
                #     game.start_time = start_time
                #     game.mlb_game_num = game_num
                #     game.away_team = away_team
                #     game.home_team = home_team
                #     game.status = status
                # else:
                #     game = ProGame_MLB(
                #         season = self,
                #         game_type = game_type,
                #         mlb_game_id = game_pk,
                #         start_time = start_time,
                #         away_team = away_team,
                #         home_team = home_team,
                #         mlb_game_num = game_num,
                #         status = status
                #     )

                # if "linescore" in g:
                #     game.update_line_score(g["linescore"])
                # ]
                # raise Exception
                hide_spoilers = set([away_abbrev, home_abbrev]).intersection(
                    set(config.get("hide_spoiler_teams", [])))

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
                    # line = LineScoreDisplay.from_mlb_api(g["linescore"])
                    line = line_score
                )

    def keypress(self, size, key):

        if key == "w":
            self._emit("watch", self.selection.data.game_id)
            # self.watch(self.selection)
        else:
            return super(GamesDataTable, self).keypress(size, key)

        # # raise Exception(self.game_id)
        # cmd = [
        #     "mlbplay",
        #     "--beginning",
        #     str(selection.data.game_id)
        # ]
        # # raise Exception(cmd)

        # proc = subprocess.Popen(cmd)
        # # proc.wait()

class Toolbar(urwid.WidgetWrap):

    def __init__(self):

        self.live_stream_dropdown = Dropdown([
            "from beginning",
            "live"
        ], label="Live streams: ")

        self.resolution_dropdown = Dropdown([
            "720p",
            "720p_alt",
            "540p",
            "504p",
            "360p",
            "288p",
            "224p"
        ], label="resolution")

        self.columns = urwid.Columns([
            (36, self.live_stream_dropdown),
            (20, self.resolution_dropdown),
            ("weight", 1, urwid.Padding(urwid.Text("")))
        ])
        self.filler = urwid.Filler(self.columns)
        super(Toolbar, self).__init__(self.filler)

    @property
    def resolution(self):
        return (self.resolution_dropdown.selected_label)

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
        # raise Exception(game_id)
        play.play_stream(
            game_id,
            self.toolbar.resolution,
            self.toolbar.start_from_beginning,
        )



def main():

    global options
    global config

    load_config()

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    options, args = parser.parse_known_args()

    if options.verbose:
        import logging
        global logger
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s [%(levelname)8s] %(message)s",
                                      datefmt='%Y-%m-%d %H:%M:%S')
        fh = logging.FileHandler("mlb.log")
        fh.setFormatter(formatter)
        logger.addHandler(fh)


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

    frame = urwid.Frame(view)

    def global_input(key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()
        else:
            return False

    loop = urwid.MainLoop(frame,
                          palette,
                          screen=screen,
                          unhandled_input=global_input,
                          pop_ups=True
    )
    loop.run()

if __name__ == "__main__":
    main()
