import hashlib
import json


def identity(kind: str, *parts) -> str:
    content = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{kind}_{hashlib.sha256(content.encode()).hexdigest()}"
