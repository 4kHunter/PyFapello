import sqlite3
from types import TracebackType


class Database:
    def __init__(self) -> None:
        self.connection = sqlite3.connect("fapello.db")
        self.cursor = self.connection.cursor()
        self.create_table()

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.connection.close()

    def create_table(self) -> None:
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS fapello (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def insert_many(self, links: list[str]) -> None:
        self.cursor.executemany(
            """
            INSERT INTO fapello (link) VALUES (?)
            ON CONFLICT(link) DO NOTHING
            """,
            [(link,) for link in links],
        )
        self.connection.commit()
