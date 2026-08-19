#!/usr/bin/env python3
"""Inline the extracted run paths into the atlas template.

  python3 scripts/run_atlas/extract_paths.py /tmp/process_paths.json
  python3 scripts/run_atlas/build.py /tmp/process_paths.json /tmp/run_atlas.html

Set MARO_WORKSPACE to point the extractor at a workspace other than
~/maro-box-copy/workspace (e.g. ~/.maro/workspace on the runtime box).
"""
import pathlib, sys

here = pathlib.Path(__file__).parent
data = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "process_paths.json")
dest = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "run_atlas.html")
html = here.joinpath("template.html").read_text().replace("__DATA__", data.read_text())
dest.write_text(html)
print(f"wrote {dest} ({len(html)/1e6:.2f} MB)")
