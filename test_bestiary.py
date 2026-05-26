import time
import sys
sys.path.append("/home/wa/VTTAI2")

from npc_bestiary_manager import _load_bestiary

start = time.time()
_load_bestiary()
end = time.time()
print(f"Time to load bestiary: {end - start:.3f} seconds")
