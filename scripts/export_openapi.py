"""Write the API's OpenAPI spec to docs/openapi.json.

The mobile app generates its TypeScript types from this file
(`npm run gen:api` in theblockgym-app). Re-run after changing any route or
schema the app uses, and commit the result:

    python scripts/export_openapi.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app  # noqa: E402

out = os.path.join(os.path.dirname(__file__), "..", "docs", "openapi.json")
with open(out, "w") as f:
    json.dump(app.openapi(), f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"Wrote {os.path.relpath(out)}")
