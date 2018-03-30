mlbstreamer
===========

It consists of two programs: mlbplay and mlbstreamer. mlbplay is a bit like the program of the same name in mlbviewer -- it's a command-line program to play a single game. mlbstreamer is more like mlbviewer itself, in that it's a console user interface that allows you to browse the schedule and play games from there.

The mlbstreamer program is definitely a work in progress, so right now it's an optional install. Well, it will be installed by default, but its dependencies won't, so it won't run.

If you want to install without those dependencies just to use the mlbplay command-line program, run:

```
pip install "git+https://github.com/tonycpsu/mlbstreamer" --process-dependency-links
```

If you want the whole thing, including the mlbstreamer console UI dependencies, run:

```
pip install "git+https://github.com/tonycpsu/mlbstreamer/#egg=mlbstreamer[gui]" --process-dependency-links
```

The first thing you'll need to do is configure your username, password, etc. To do that, run:

```
mlbplay --init-config
```

The program should ask you for your username and password, then try to find your media player (it just looks for mpv or vlc right now.). If it doesn't find it, you can enter the full path to whatever you're using. If your player worked with mlbviewer, it should work with mlbstreamer. It'll also ask you for your time zone so that game times are displayed properly.

Once that's done, you should be able to play a team's games by running
the following command, where TEAM is the team code for the team's game
you want to watch:

```
mlbplay [TEAM]
```

If you want to watch a game for a different date, run with the -d option, e.g:

```
mlbplay -d 2018-02-24 phi
```

You can also save the stream to disk with the -s option, e.g:

```
mlbplay -s ~/mlb-phi.ogg phi
```

The mlbstreamer console UI may or may not work for everyone right now. If it does, it'll show you a schedule view. Press "w" to watch a game, left/right arrows to browse days, "t" to go to today's games. The log window at the bottom should tell you if there are any errors, like if the game doesn't have a stream.
