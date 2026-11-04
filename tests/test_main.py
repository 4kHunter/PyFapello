import asyncio
import runpy
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

import main
import utils
from tests.conftest import LocalServer, Reply, media
from utils.scrapers import FappeloScraper

LINK = "https://fapello.com/model/"


class FakeScraper:
    urls: ClassVar[list[str]] = []
    links: ClassVar[list[str]] = []

    def __init__(self, link: str) -> None:
        type(self).links.append(link)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def fetch_urls(self) -> list[str]:
        return list(type(self).urls)


class FakeDownloader:
    result: ClassVar[list[str]] = []
    error: ClassVar[Exception | None] = None
    created: ClassVar[list[tuple[list[str], Path]]] = []

    def __init__(self, urls: list[str], folder: Path) -> None:
        type(self).created.append((urls, folder))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def download_all(self) -> list[str]:
        error = type(self).error
        if error is not None:
            raise error
        return list(type(self).result)


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[FakeDownloader]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "FappeloScraper", FakeScraper)
    monkeypatch.setattr(main, "Downloader", FakeDownloader)
    FakeScraper.urls = ["https://h/a_300px.jpg", "https://h/b_300px.jpg"]
    FakeScraper.links = []
    FakeDownloader.result = []
    FakeDownloader.error = None
    FakeDownloader.created = []
    return FakeDownloader


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", *args])
    asyncio.run(main.main())


def saved_links(directory: Path) -> list[str]:
    with closing(sqlite3.connect(directory / "fapello.db")) as connection:
        return [link for (link,) in connection.execute("SELECT link FROM fapello ORDER BY id")]


class TestParseArgs:
    def test_parses_required_arguments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py", "-l", LINK, "-f", "out"])

        args = main.parse_args()

        assert (args.link, args.folder, args.save) == (LINK, "out", False)

    def test_save_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py", "--link", LINK, "--folder", "out", "--save"])

        assert main.parse_args().save is True

    @pytest.mark.parametrize("argv", [["-f", "out"], ["-l", LINK]])
    def test_link_and_folder_are_required(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py", *argv])

        with pytest.raises(SystemExit) as exit_info:
            main.parse_args()

        assert exit_info.value.code == 2


class TestResolveFolder:
    def test_absolute_path_is_kept(self, tmp_path: Path) -> None:
        assert main.resolve_folder(str(tmp_path / "x")) == tmp_path / "x"

    def test_relative_path_is_resolved_next_to_main(self) -> None:
        assert main.resolve_folder("downloads") == Path(main.__file__).resolve().parent / "downloads"

    def test_home_is_expanded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        assert main.resolve_folder("~/media") == tmp_path / "media"


class TestSaveLinks:
    def test_persists_links(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        main.save_links(["https://h/a.jpg", "https://h/b.jpg"])

        assert saved_links(tmp_path) == ["https://h/a.jpg", "https://h/b.jpg"]


class TestCliValidation:
    @pytest.mark.parametrize(
        "link",
        [
            "ftp://fapello.com/model/",
            "https://example.com/model/",
            "https://www.fapello.com/model/",
            "https://fapello.com/",
            "https://fapello.com",
            "fapello.com/model",
        ],
    )
    def test_invalid_links_are_rejected_before_any_work(
        self, fakes: type[FakeDownloader], monkeypatch: pytest.MonkeyPatch, link: str
    ) -> None:
        with pytest.raises(ValueError, match="Invalid profile link"):
            run_cli(monkeypatch, "-l", link, "-f", "out", "-s")

        assert FakeScraper.links == []
        assert fakes.created == []


class TestCliFlow:
    def test_no_media_found_skips_download_and_database(
        self,
        fakes: type[FakeDownloader],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        FakeScraper.urls = []

        run_cli(monkeypatch, "-l", LINK, "-f", "out", "-s")

        assert capsys.readouterr().out == "No media found.\n"
        assert fakes.created == []
        assert not (tmp_path / "fapello.db").exists()

    def test_downloads_scraped_urls_into_resolved_folder(
        self, fakes: type[FakeDownloader], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "target"

        run_cli(monkeypatch, "-l", LINK, "-f", str(target))

        assert FakeScraper.links == [LINK]
        assert fakes.created == [(FakeScraper.urls, target)]

    def test_without_save_flag_nothing_is_recorded(
        self, fakes: type[FakeDownloader], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fakes.result = ["https://h/a.jpg"]

        run_cli(monkeypatch, "-l", LINK, "-f", "out")

        assert not (tmp_path / "fapello.db").exists()

    def test_save_records_only_urls_returned_by_downloader(
        self, fakes: type[FakeDownloader], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fakes.result = ["https://h/a.jpg"]

        run_cli(monkeypatch, "-l", LINK, "-f", "out", "-s")

        assert saved_links(tmp_path) == ["https://h/a.jpg"]

    def test_save_with_no_successful_download_records_nothing(
        self, fakes: type[FakeDownloader], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run_cli(monkeypatch, "-l", LINK, "-f", "out", "-s")

        assert not (tmp_path / "fapello.db").exists()

    def test_downloader_error_propagates_and_nothing_is_recorded(
        self, fakes: type[FakeDownloader], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fakes.error = RuntimeError("downloader crashed")

        with pytest.raises(RuntimeError, match="downloader crashed"):
            run_cli(monkeypatch, "-l", LINK, "-f", "out", "-s")

        assert not (tmp_path / "fapello.db").exists()


class TestEntryPoint:
    def test_running_as_script_executes_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        received: list[Any] = []

        def fake_run(coroutine: Any) -> None:
            received.append(coroutine)
            coroutine.close()

        monkeypatch.setattr(asyncio, "run", fake_run)

        runpy.run_path(main.__file__, run_name="__main__")

        assert len(received) == 1
        assert received[0].__qualname__ == "main"

    def test_importing_main_does_not_run_anything(self) -> None:
        namespace = runpy.run_path(main.__file__, run_name="not_main")

        assert callable(namespace["main"])

    def test_utils_package_exports(self) -> None:
        assert set(utils.__all__) == {"Database", "Downloader", "FappeloScraper"}
        assert utils.Database.__name__ == "Database"


class TestEndToEnd:
    def test_real_scraper_and_downloader_record_final_urls_only(
        self,
        server: LocalServer,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        no_retry_sleep: list[float],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(FappeloScraper, "BASE_URL", server.url("/ajax/model/{name}/"))
        listing = (
            f'<a href="/p/1"><img src="{server.url("/i/full_300px.jpg")}"></a>'
            f'<a href="/p/2"><img src="{server.url("/i/thumb_300px.jpg")}"></a>'
            f'<a href="/p/3"><img src="{server.url("/i/broken_300px.jpg")}"></a>'
            f'<a href="/p/4"><img src="{server.url("/j/full_300px.jpg")}"></a>'
            '<a href="/v/1"><img src="/icons/icon-play.png"></a>'
        )
        server.routes["/ajax/model/model/page-1/"] = Reply(body=listing.encode())
        server.routes["/ajax/model/model/page-2/"] = Reply(body=b"")
        server.routes["/v/1"] = Reply(body=f'<source src="{server.url("/m/clip.mp4")}">'.encode())
        server.routes["/m/clip.mp4"] = Reply(body=b"video", headers={"Content-Type": "video/mp4"})
        server.routes["/i/full.jpg"] = media(b"full-i")
        server.routes["/j/full.jpg"] = media(b"full-j")
        server.routes["/i/thumb_300px.jpg"] = media(b"thumb")
        server.routes["/i/broken.jpg"] = Reply(status=403)
        server.routes["/i/broken_300px.jpg"] = media(b"never-requested")
        out = tmp_path / "out"

        run_cli(monkeypatch, "-l", LINK, "-f", str(out), "-s")

        assert sorted(saved_links(tmp_path)) == sorted(
            [
                server.url("/i/full.jpg"),
                server.url("/j/full.jpg"),
                server.url("/i/thumb_300px.jpg"),
                server.url("/m/clip.mp4"),
            ]
        )
        assert server.count("/i/broken_300px.jpg") == 0
        assert sorted(p.name for p in (out / "Images").iterdir()) == [
            "full.jpg",
            "full_1.jpg",
            "thumb_300px.jpg",
        ]
        assert {p.read_bytes() for p in (out / "Images").glob("full*.jpg")} == {b"full-i", b"full-j"}
        assert (out / "Videos" / "clip.mp4").read_bytes() == b"video"
        assert not list(out.rglob("*.part"))
