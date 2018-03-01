#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import sys
from os import path
from glob import glob

name = "mlbstreamer"
setup(name=name,
      version="0.0.1",
      description="MLB.tv Stream Browser",
      author="Tony Cebzanov",
      author_email="tonycpsu@gmail.com",
      url="https://github.com/tonycpsu/mlbstreamer",
      classifiers=[
          "Environment :: Console",
          "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
          "Intended Audience :: End Users/Desktop"
      ],
      packages=find_packages(),
      data_files=[
          ("share/doc/%s" % name,
           ["LICENSE","README.md"]),
      ],
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
          "streamlink",
      ],
      extras_require={
          'gui':  [
              "urwid",
              "urwid_utils==0.0.5a",
              "panwid==0.2.1",
          ],
      },
      dependency_links=[
          "https://github.com/tonycpsu/urwid_utils/tarball/master#egg=urwid_utils-0.0.5a",
          "https://github.com/tonycpsu/panwid/tarball/master#egg=panwid-0.2.1",
      ],
      entry_points = {
          "console_scripts": [
              "mlbstreamer=mlbstreamer.__main__:main",
              "mlbplay=mlbstreamer.play:main"
          ],
      }
     )
