import sys
import os

# Add backend directory to Python path so all modules resolve correctly
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

from main import app  # noqa: F401  — Vercel looks for `app` in this module
