import sys
from unittest.mock import MagicMock
sys.modules['google'] = MagicMock()
import autogen
print("Autogen imported without ANY google packages!")
