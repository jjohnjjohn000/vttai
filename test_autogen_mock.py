import sys
from unittest.mock import MagicMock
import time

sys.modules['google.cloud.aiplatform'] = MagicMock()
sys.modules['vertexai'] = MagicMock()

t0 = time.time()
print("Importing autogen...")
import autogen
print(f"Done in {time.time() - t0:.2f}s")
