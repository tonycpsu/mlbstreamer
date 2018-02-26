import os
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader

import yaml

CONFIG_DIR=os.path.expanduser("~/.mlb")
CONFIG_FILE=os.path.join(CONFIG_DIR, "config.yaml")


def load_config():
    global config
    if not os.path.exists(CONFIG_FILE):
        raise Exception("config file %s not found" %(CONFIG_FILE))
    config = yaml.load(open(CONFIG_FILE), Loader=AttrDictYAMLLoader)

def save_config():
    global config
    with open(CONFIG_FILE, 'w') as outfile:
        yaml.dump(config, outfile, default_flow_style=False)

load_config()
