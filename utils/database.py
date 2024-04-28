import sqlite3
from types import TracebackType


class Database:

    def __init__(self) -> None:
        self.connection = sqlite3.connect("fapello.db")
        self.cursor = self.connection.cursor()
        self.create_table()

    def __enter__(self) -> "Database":
        return self

    def __exit__(
        self, exc_type: type[Exception], exc_val: Exception, exc_tb: TracebackType
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

    def insert(self, link: str) -> None:
        self.cursor.execute(
            """
            INSERT INTO fapello (link) VALUES (?)
            """,
            (link,),
        )
        self.connection.commit()

    def get_existing_urls(self, urls: list[str]) -> list[str]:
        placeholders = ",".join("?" for _ in urls)
        query = f"SELECT link FROM fapello WHERE link IN ({placeholders})"  # noqa: S608
        self.cursor.execute(query, urls)
        return [row[0] for row in self.cursor.fetchall()]
