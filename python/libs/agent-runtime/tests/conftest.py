import sys
from pathlib import Path

# The dev package (dev/agent_dev) is tested from here too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
