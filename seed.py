"""Seed demo admin, BLO, and voter login accounts (idempotent).

Run after db/schema.sql and etl/load_roll.py.
"""
from __future__ import annotations

import os
import psycopg
from passlib.context import CryptContext

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
dsn = os.getenv("DATABASE_URL", "postgresql://sir:sir@localhost:5432/sir")
part_no = int(os.getenv("PART_NO", "271"))
assembly_constituency = int(os.getenv("ASSEMBLY_CONSTITUENCY", "174"))
part_name = os.getenv("PART_NAME", "Mahadevapura")


def seed() -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO part(part_no, assembly_constituency, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (part_no) DO NOTHING
                """,
                (part_no, assembly_constituency, part_name),
            )
            cur.execute(
                """
                INSERT INTO blo(username, full_name, part_no)
                VALUES ('blo1', 'Demo BLO One', %s), ('blo2', 'Demo BLO Two', %s)
                ON CONFLICT (username) DO NOTHING
                """,
                (part_no, part_no),
            )
            cur.execute(
                """
                INSERT INTO app_user(username, password_hash, role)
                VALUES (%s, %s, 'ADMIN')
                ON CONFLICT (username) DO NOTHING
                """,
                ("admin", pwd.hash(os.getenv("ADMIN_PASSWORD", "admin123"))),
            )

            for username in ("blo1", "blo2"):
                cur.execute("SELECT blo_id FROM blo WHERE username = %s", (username,))
                blo_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO app_user(username, password_hash, role, blo_id)
                    VALUES (%s, %s, 'BLO', %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (username, pwd.hash(f"{username}123"), blo_id),
                )

            cur.execute(
                "SELECT epic FROM voter WHERE part_no = %s ORDER BY serial_no LIMIT 10",
                (part_no,),
            )
            voters = cur.fetchall()
            for idx, (epic,) in enumerate(voters):
                blo_user = "blo1" if idx < 5 else "blo2"
                cur.execute(
                    """
                    UPDATE voter
                    SET assigned_blo_id = (SELECT blo_id FROM blo WHERE username = %s)
                    WHERE epic = %s
                    """,
                    (blo_user, epic),
                )
                cur.execute(
                    """
                    INSERT INTO app_user(username, password_hash, role, voter_epic)
                    VALUES (%s, %s, 'VOTER', %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (f"voter{idx + 1}", pwd.hash("voter123"), epic),
                )

    print(
        f"Seeded admin/admin123, BLOs blo1/blo1123 & blo2/blo2123, "
        f"and {len(voters)} demo voter account(s) (voter1..voter{len(voters)}/voter123)."
    )


if __name__ == "__main__":
    seed()
