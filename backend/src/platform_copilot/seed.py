"""Load the fake users and data products. Documents are ingested by `platform_copilot.rag.ingest`.

Usage: `uv run python -m platform_copilot.seed`
"""

import asyncio
import csv
import json
from pathlib import Path

import psycopg

from platform_copilot.config import get_settings

SEED_DIR = Path(__file__).resolve().parents[3] / "seed"


async def load_users_and_products(conn: psycopg.AsyncConnection[object], seed_dir: Path) -> int:
    users = json.loads((seed_dir / "users.json").read_text(encoding="utf-8"))
    with (seed_dir / "products.csv").open(encoding="utf-8") as handle:
        products = list(csv.DictReader(handle))
    async with conn.transaction():
        await conn.execute(
            "TRUNCATE access_requests, data_products, users RESTART IDENTITY CASCADE"
        )
        for user in users:
            await conn.execute(
                "INSERT INTO users (id, name, groups) VALUES (%s, %s, %s)",
                (user["id"], user["name"], user["groups"]),
            )
        for row in products:
            await conn.execute(
                "INSERT INTO data_products (name, domain, country, owner, description, pii)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    row["name"],
                    row["domain"],
                    row["country"],
                    row["owner"],
                    row["description"],
                    row["pii"] == "true",
                ),
            )
    return len(products)


async def main() -> None:
    settings = get_settings()
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        count = await load_users_and_products(conn, SEED_DIR)
    print(f"Loaded {count} data products")


if __name__ == "__main__":
    asyncio.run(main())
