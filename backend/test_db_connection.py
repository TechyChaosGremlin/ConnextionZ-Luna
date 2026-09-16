import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect(
        user="postgres",
        password="Silverrose7",
        host="localhost",
        port=5432,
        database="connextionz",
    )

    print("ALEMBIC VERSION:")
    version = await conn.fetchval(
        "SELECT version_num FROM alembic_version"
    )
    print(version)

    print("\nPROFILE COLUMNS:")
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'profiles'
        ORDER BY ordinal_position
        """
    )

    for row in rows:
        print(" -", row["column_name"])

    await conn.close()

asyncio.run(main())