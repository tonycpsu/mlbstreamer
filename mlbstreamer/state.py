from memoize import *
session = None
store = {}
memo = Memoizer(store)
memo.regions['short'] = {'max_age': 60}
memo.regions['long'] = {'max_age': 900}
