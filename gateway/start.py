import subprocess
from pathlib import Path
import sys
subprocess.call([sys.executable, str(Path(__file__).with_name("server.py"))], cwd=Path(__file__).parent)
