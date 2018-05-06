#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import sys
from os import path
from glob import glob

name = "mlbstreamer"
setup(name=name,
      version="0.0.10",
      description="MLB.tv Stream Browser",
      author="Tony Cebzanov",
      author_email="tonycpsu@gmail.com",
      url="https://github.com/tonycpsu/mlbstreamer",
      classifiers=[
          "Environment :: Console",
          "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
          "Intended Audience :: End Users/Desktop"
      ],
      license = "GPLv2",
      packages=find_packages(),
      include_package_data=True,
      install_requires = [
          "six",
          "requests",
          "lxml",
          "pytz",
          "tzlocal",
          "pymemoize",
          "orderedattrdict",
          "pyyaml",
          "py-dateutil",
          "streamlink>=0.11.0",
          "prompt_toolkit",
          "urwid",
          "urwid_utils>=0.1.2",
          "panwid>=0.2.4"
      ],
      test_suite="test",
      entry_points = {
          "console_scripts": [
              "mlbstreamer=mlbstreamer.__main__:main",
              "mlbplay=mlbstreamer.play:main"
          ],
      }
     )
