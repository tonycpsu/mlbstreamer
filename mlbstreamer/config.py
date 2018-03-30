from __future__ import unicode_literals
import os
import errno
import pytz
try:
    from collections.abc import MutableMapping
except ImportError:
    from collections import MutableMapping
import yaml
from orderedattrdict import AttrDict
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
import distutils.spawn
import tzlocal

from prompt_toolkit import prompt
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.shortcuts import confirm
from prompt_toolkit.shortcuts import prompt
import getpass

CONFIG_DIR=os.path.expanduser("~/.config/mlbstreamer")
CONFIG_FILE=os.path.join(CONFIG_DIR, "config.yaml")
LOG_FILE=os.path.join(CONFIG_DIR, "mlbstreamer.log")

KNOWN_PLAYERS = ["mpv", "vlc"]

settings = None


class NotEmptyValidator(Validator):

    def validate(self, document):
        text = document.text
        if not len(text):
            raise ValidationError(message="Please supply a value")

class RangeNumberValidator(Validator):

    def __init__(self, minimum=None, maximum=None):
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, document):

        text = document.text

        if not text:
            raise ValidationError(message="Please supply a value")

        if text.isdigit():
            value = int(text)
        else:
            i = 0

            raise ValidationError(
                message='Please enter an integer.'
            )

        if self.minimum and value < self.minimum:
            raise ValidationError(
                message="Value must be greater than %s" %(self.minimum)
            )

        if self.maximum and value > self.maximum:
            raise ValidationError(
                message="Value must be less than %s" %(self.maximum)
            )


class Config(MutableMapping):

    def __init__(self, config_file):

        self._config = None
        self._config_file = config_file

    def init_config(self):

        from .session import MLBSession, MLBSessionException

        def mkdir_p(path):
            try:
                os.makedirs(path)
            except OSError as exc:  # Python >2.5
                if not (exc.errno == errno.EEXIST and os.path.isdir(path)):
                    raise

        def find_players():
            for p in KNOWN_PLAYERS:
                player = distutils.spawn.find_executable(p)
                if player:
                    yield player

        MLBSession.destroy()
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)

        self._config = AttrDict()
        time_zone = None
        player = None
        mkdir_p(CONFIG_DIR)

        while True:
            self.username = prompt(
                "MLB.tv username: ",
                validator=NotEmptyValidator())
            self.password =  prompt(
                'Enter password: ',
                is_password=True, validator=NotEmptyValidator())
            try:
                s = MLBSession(self.username, self.password)
                s.login()
                break
            except MLBSessionException:
                print("Couldn't login to MLB, please check your credentials.")
                continue

        tz_local = tzlocal.get_localzone().zone

        # password = prompt("MLB.tv password (will be stored in clear text!): ")
        found_players = list(find_players())
        if not found_players:
            print("no known media players found")
        else:
            print("found the following media players")
            print("\n".join(
                [ "\t%d: %s" %(n, p)
                  for n, p in enumerate(
                          ["My player is not listed"] + found_players
                  )]))
            choice = int(
                prompt(
                    "Select the number corresponding to your preferred player,\n"
                    "or 0 if your player is not listed: ",
                    validator=RangeNumberValidator(maximum=len(found_players))))
            if choice:
                player = found_players[choice-1]

        while not player:
            response = prompt("Please enter the path to your media player: ")
            player = distutils.spawn.find_executable(response)
            if not player:
                print("Couldn't locate player '%s'" %(response))

        player_args = prompt(
            "If you need to pass additional arguments to your media "
            "player, enter them here: ")
        if player_args:
            player = " ".join([player, player_args])

        self.player = player

        print("Your system time zone seems to be %s." %(tz_local))
        if not confirm("Is that the time zone you'd like to use? (y/n) "):
            while not time_zone:
                response = prompt("Enter your preferred time zone: ")
                if response in pytz.common_timezones:
                    time_zone = response
                    break
                elif confirm("Can't find time zone %s: are you sure? (y/n) "):
                    time_zone = response
                    break

        else:
            time_zone = tz_local

        self.time_zone = time_zone
        self.save()

    def load(self):
        if not os.path.exists(self._config_file):
            raise Exception("config file %s not found" %(CONFIG_FILE))

        config = yaml.load(open(self._config_file), Loader=AttrDictYAMLLoader)
        if config.get("time_zone"):
            config.tz = pytz.timezone(config.time_zone)
        self._config = config

    def save(self):

        with open(self._config_file, 'w') as outfile:
            yaml.dump(self._config, outfile, default_flow_style=False)

    def __getattr__(self, name):
        return self._config.get(name, None)

    def __setattr__(self, name, value):

        if not name.startswith("_"):
            self._config[name] = value
        object.__setattr__(self, name, value)

    def __getitem__(self, key):
        return self._config[key]

    def __setitem__(self, key, value):
        self._config[key] = value

    def __delitem__(self, key):
        del self._config[key]

    def __len__(self):
        return len(self._config)

    def __iter__(self):
        return iter(self._config)


settings = Config(CONFIG_FILE)

__all__ = [
    "CONFIG_DIR",
    "config",
    "settings"
]
