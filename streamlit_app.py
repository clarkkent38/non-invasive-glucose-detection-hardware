"""
streamlit_app.py — Streamlit Cloud entry point
================================================
Streamlit Cloud requires a file at the repo root. This file delegates
everything to app/live_dashboard.py so there is no code duplication.

In Streamlit Cloud's deployment form, set:
  Main file path: streamlit_app.py
  (leave Branch as: main)

Locally you can still run either:
  streamlit run streamlit_app.py
  streamlit run app/live_dashboard.py
"""

# runpy re-executes the target module in-place, which is exactly how
# Streamlit expects entry-point files to work — all st.* calls in
# live_dashboard.py fire in the same Streamlit execution context.
import runpy, pathlib, sys

# Make sure the project root is on the path (same as live_dashboard.py does)
ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

runpy.run_path(str(ROOT / "app" / "live_dashboard.py"), run_name="__main__")
