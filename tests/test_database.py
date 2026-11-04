import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from utils.database import Database


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def rows(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as connection:
        return [link for (link,) in connection.execute("SELECT link FROM fapello ORDER BY id")]


class TestDatabase:
    def test_creates_database_and_table_in_working_directory(self, isolated_cwd: Path) -> None:
        with Database():
            pass

        with closing(sqlite3.connect(isolated_cwd / "fapello.db")) as connection:
            columns = {row[1]: row for row in connection.execute("PRAGMA table_info(fapello)")}

        assert set(columns) == {"id", "link", "created_at"}
        assert columns["link"][3] == 1

    def test_inserts_links_in_order_with_timestamp(self, isolated_cwd: Path) -> None:
        with Database() as database:
            database.insert_many(["https://h/b.jpg", "https://h/a.jpg"])

        assert rows(isolated_cwd / "fapello.db") == ["https://h/b.jpg", "https://h/a.jpg"]
        with closing(sqlite3.connect(isolated_cwd / "fapello.db")) as connection:
            assert connection.execute("SELECT COUNT(*) FROM fapello WHERE created_at IS NOT NULL").fetchone() == (2,)

    def test_duplicates_are_ignored_within_and_across_calls(self, isolated_cwd: Path) -> None:
        with Database() as database:
            database.insert_many(["https://h/a.jpg", "https://h/a.jpg"])
            database.insert_many(["https://h/a.jpg", "https://h/b.jpg"])

        assert rows(isolated_cwd / "fapello.db") == ["https://h/a.jpg", "https://h/b.jpg"]

    def test_empty_list_inserts_nothing(self, isolated_cwd: Path) -> None:
        with Database() as database:
            database.insert_many([])

        assert rows(isolated_cwd / "fapello.db") == []

    def test_data_persists_across_instances(self, isolated_cwd: Path) -> None:
        with Database() as database:
            database.insert_many(["https://h/a.jpg"])
        with Database() as database:
            database.insert_many(["https://h/b.jpg"])

        assert rows(isolated_cwd / "fapello.db") == ["https://h/a.jpg", "https://h/b.jpg"]

    def test_connection_is_closed_on_exit_even_after_error(self) -> None:
        database = Database()
        connection = database.connection

        with pytest.raises(RuntimeError, match="boom"), database:
            raise RuntimeError("boom")

        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
