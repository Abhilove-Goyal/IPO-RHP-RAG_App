import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.r2_storage import configuration_status, delete_object, download_bytes, object_exists, upload_bytes


def main() -> None:
    key = f"tests/r2-connectivity-{uuid.uuid4()}.bin"
    payload = b"decision-analyst-r2-connectivity"
    start = time.perf_counter()
    deleted = False
    try:
        upload_bytes(payload, key, content_type="application/octet-stream")
        if not object_exists(key):
            raise RuntimeError("uploaded object was not found")
        if download_bytes(key) != payload:
            raise RuntimeError("downloaded object content did not match")
    finally:
        delete_object(key)
        deleted = True
    elapsed = time.perf_counter() - start
    print("R2_CONNECTIVITY_PASS" if deleted else "R2_CONNECTIVITY_FAIL", f"elapsed_seconds={elapsed:.3f}")


if __name__ == "__main__":
    main()
