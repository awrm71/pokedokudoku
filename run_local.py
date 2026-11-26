"""
run_local.py — start Route 81 on this computer and open the Cube page.

Open this file in VS Code and press the Run button (▷, top right), or run
`python run_local.py` from the repo root. Stop it with Ctrl+C in the terminal.
Needs the packages in requirements.txt in the Python that runs it.
"""

import os
import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8000
PAGE = f"http://{HOST}:{PORT}/cube.html"

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    threading.Timer(4.0, lambda: webbrowser.open(PAGE)).start()
    print(f"Route 81 is starting. The Cube opens at {PAGE}")
    uvicorn.run("api_main:app", host=HOST, port=PORT, reload=True)
