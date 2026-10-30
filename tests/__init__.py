"""The tests import the program's modules by name, the way the program does: app/ goes on the path
first. Run them from the repository's folder with `py -m unittest`, or one file with
`py -m unittest tests.test_sessions`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
