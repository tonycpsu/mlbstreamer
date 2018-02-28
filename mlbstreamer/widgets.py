import urwid
import panwid

class ScrollbackListBox(urwid.WidgetWrap):

    signals = ["updated"]

    def __init__(self, update_interval=1):
        self.update_interval = update_interval
        self._results = ScrollbackListWalker(1000)
        self._fields = []
        # self.lock = threading.Lock()
        self.collapsed = True
        self.hidefields = []
        self.filters = []
        self.pattern = None
        self.updated = False
        self.update_timer = None
        self._listbox = urwid.ListBox(self._results)
        urwid.WidgetWrap.__init__(self, self._listbox)

    def _modified(self):
        self._listbox.body._modified()

    def append(self, label):

        result = urwid.Text(label)
        self._results.append(result)
        self._listbox._invalidate()
        self.updated = True


    def keypress(self, size, key):

        if key == 'up' or key == 'k':
            self._listbox.keypress(size, 'up')
        elif key == 'page up' or key == 'ctrl u':
            self._listbox.keypress(size, 'page up')
        elif key == 'down' or key == 'j':
            self._listbox.keypress(size, 'down')
        elif key == 'page down' or key == 'ctrl d':
            self._listbox.keypress(size, 'page down')
        elif key == 'home':
            if len(self._listbox.body):
                self._listbox.focus_position = 0
                self.listbox._invalidate()
        elif key == 'end':
            if len(self._listbox.body):
                self._listbox.focus_position = len(self._listbox.body)-1
                self._listbox._invalidate()
        return super(ScrollbackListBox, self).keypress(size, key)

    def clear(self):
        self._results.reset()

    def on_updated(self):
        self._listbox.set_focus(len(self._results)-1)
        self._listbox._invalidate()
        state.loop.draw_screen()
        self.updated = False



class ConsoleWindow(urwid.WidgetWrap):

    def __init__(self, verbose=False):

        # self.fd = fd
        self.verbose = verbose
        self.listbox =  panwid.listbox.ScrollingListBox([])
        super(ConsoleWindow, self).__init__(self.listbox)

    def log_message(self, msg):
        self.listbox.body.append(urwid.Text(msg.rstrip()))

    def mark(self):
        self.log_message("-" * 80)

    def selectable(self):
        return True

    def keypress(self, size, key):
        if key == "m":
            self.mark()
        # return super(ConsoleWindow, self).kepyress(size, key)
        return key
