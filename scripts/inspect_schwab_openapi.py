from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SPECS = {
    "market_data": ROOT / "docs" / "schwab" / "openapi" / "schwab-openapi-market-data-api.json",
    "retail": ROOT / "docs" / "schwab" / "openapi" / "schwab-openapi-retail-api.json",
}


def main() -> None:
    for name, path in SPECS.items():
        with path.open("r", encoding="utf-8-sig") as f:
            spec = json.load(f)

        print(f"\n{name.upper()}")
        print("=" * 80)
        print("Title:", spec.get("info", {}).get("title"))
        print("OpenAPI:", spec.get("openapi"))
        print("Servers:", [server.get("url") for server in spec.get("servers", [])])

        paths = spec.get("paths", {})
        for route, methods in paths.items():
            for method, operation in methods.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                print(f"{method.upper():6} {route}  -  {operation.get('summary', '')}")


if __name__ == "__main__":
    main()