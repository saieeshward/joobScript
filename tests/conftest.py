import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "Q-Rattor")):
    if p not in sys.path:
        sys.path.insert(0, p)
