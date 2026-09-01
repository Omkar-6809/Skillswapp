import os
import sys

# Vercel executes this file from the api/ directory. Add the project root
# so the Flask app and its templates/static files can be imported correctly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app

# Vercel uses this Flask WSGI application as a Python Function.
