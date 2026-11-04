import logging

import pytest
from bs4 import BeautifulSoup
from niquests.exceptions import ChunkedEncodingError, ConnectionError as NiquestsConnectionError, HTTPError, Timeout

from tests.conftest import LocalServer, Reply
from utils.scrapers import FappeloScraper

MODEL_PATH = "/ajax/model/model/"


def page(*items: str) -> Reply:
    return Reply(body="".join(items).encode(), headers={"Content-Type": "text/html"})


def image(src: str, href: str = "/post") -> str:
    return f'<a href="{href}"><img src="{src}"></a>'


def video(href: str) -> str:
    return f'<a href="{href}"><img src="/icons/icon-play.png"></a>'


def source(src: str) -> Reply:
    return page(f'<video><source src="{src}"></video>')


@pytest.fixture
def scraper_server(server: LocalServer, monkeypatch: pytest.MonkeyPatch) -> LocalServer:
    monkeypatch.setattr(FappeloScraper, "BASE_URL", server.url("/ajax/model/{name}/"))
    return server


def page_path(number: int) -> str:
    return f"{MODEL_PATH}page-{number}/"


class TestParsing:
    def test_model_name_comes_from_link_path(self) -> None:
        assert FappeloScraper("https://fapello.com/some-model/").get_model_name() == "some-model"

    def test_image_urls_skip_video_icons_and_anchors_without_images(self) -> None:
        soup = BeautifulSoup(
            image("/img/a_300px.jpg")
            + video("/v/1")
            + '<a href="/text-only">no image</a>'
            + image("https://cdn.example/b_300px.jpg"),
            "html.parser",
        )

        urls = FappeloScraper("https://fapello.com/m/").get_image_urls(soup, "https://fapello.com/ajax/")

        assert urls == ["https://fapello.com/img/a_300px.jpg", "https://cdn.example/b_300px.jpg"]

    def test_video_pages_are_resolved_to_absolute_unique_urls(self) -> None:
        soup = BeautifulSoup(video("/v/1") + video("/v/1") + image("/a.jpg") + video("/v/2"), "html.parser")

        pages = FappeloScraper("https://fapello.com/m/").get_video_pages(soup, "https://fapello.com/ajax/")

        assert pages == {"https://fapello.com/v/1", "https://fapello.com/v/2"}


class TestPagination:
    async def test_collects_every_page_until_empty_body(self, scraper_server: LocalServer) -> None:
        scraper_server.routes[page_path(1)] = page(image("/a.jpg"))
        scraper_server.routes[page_path(2)] = page(image("/b.jpg"), image("/c.jpg"))
        scraper_server.routes[page_path(3)] = page("   ")

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            urls = await scraper.fetch_urls()

        assert urls == [scraper_server.url(p) for p in ("/a.jpg", "/b.jpg", "/c.jpg")]
        assert scraper_server.hits == [page_path(1), page_path(2), page_path(3)]

    async def test_stops_on_page_without_images(self, scraper_server: LocalServer) -> None:
        scraper_server.routes[page_path(1)] = page(image("/a.jpg"))
        scraper_server.routes[page_path(2)] = page("<p>nothing here</p>")
        scraper_server.routes[page_path(3)] = page(image("/never.jpg"))

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            urls = await scraper.fetch_urls()

        assert urls == [scraper_server.url("/a.jpg")]
        assert page_path(3) not in scraper_server.hits

    async def test_stops_when_server_keeps_returning_the_same_page(self, scraper_server: LocalServer) -> None:
        repeated = page(image("/a.jpg"), image("/b.jpg"))
        for number in range(1, 50):
            scraper_server.routes[page_path(number)] = repeated

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            urls = await scraper.fetch_urls()

        assert urls == [scraper_server.url("/a.jpg"), scraper_server.url("/b.jpg")]
        assert scraper_server.hits == [page_path(1), page_path(2)]

    async def test_stops_on_cycle_between_pages(self, scraper_server: LocalServer) -> None:
        scraper_server.routes[page_path(1)] = page(image("/a.jpg"))
        scraper_server.routes[page_path(2)] = page(image("/b.jpg"))
        scraper_server.routes[page_path(3)] = page(image("/a.jpg"))
        scraper_server.routes[page_path(4)] = page(image("/never.jpg"))

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            urls = await scraper.fetch_urls()

        assert urls == [scraper_server.url("/a.jpg"), scraper_server.url("/b.jpg")]
        assert scraper_server.hits == [page_path(1), page_path(2), page_path(3)]

    async def test_http_error_on_a_listing_page_is_raised(self, scraper_server: LocalServer) -> None:
        scraper_server.routes[page_path(1)] = Reply(status=500)

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            with pytest.raises(HTTPError):
                await scraper.fetch_urls()


class TestVideos:
    async def test_images_come_first_then_videos_and_pages_are_fetched_once(self, scraper_server: LocalServer) -> None:
        scraper_server.routes[page_path(1)] = page(video("/v/1"), image("/a.jpg"))
        scraper_server.routes[page_path(2)] = page(video("/v/1"), video("/v/2"), image("/b.jpg"))
        scraper_server.routes[page_path(3)] = page("")
        scraper_server.routes["/v/1"] = source("/media/1.mp4")
        scraper_server.routes["/v/2"] = source("https://cdn.example/2.mp4")

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            urls = await scraper.fetch_urls()

        assert urls == [
            scraper_server.url("/a.jpg"),
            scraper_server.url("/b.jpg"),
            scraper_server.url("/media/1.mp4"),
            "https://cdn.example/2.mp4",
        ]
        assert scraper_server.count("/v/1") == 1
        assert scraper_server.count("/v/2") == 1

    async def test_failing_video_page_does_not_lose_images_or_other_videos(
        self, scraper_server: LocalServer, caplog: pytest.LogCaptureFixture
    ) -> None:
        scraper_server.routes[page_path(1)] = page(
            image("/a.jpg"), video("/v/bad"), video("/v/good"), video("/v/challenge")
        )
        scraper_server.routes[page_path(2)] = page(image("/b.jpg"))
        scraper_server.routes[page_path(3)] = page("")
        scraper_server.routes["/v/bad"] = Reply(status=500)
        scraper_server.routes["/v/good"] = source("/media/good.mp4")
        scraper_server.routes["/v/challenge"] = Reply(headers={"cf-mitigated": "challenge"})

        with caplog.at_level(logging.WARNING, logger="utils.scrapers"):
            async with FappeloScraper("https://fapello.com/model/") as scraper:
                urls = await scraper.fetch_urls()

        assert urls == [scraper_server.url(p) for p in ("/a.jpg", "/b.jpg", "/media/good.mp4")]
        messages = [record.getMessage() for record in caplog.records]
        assert len(messages) == 2
        assert any("/v/bad" in message and "500" in message for message in messages)
        assert any("/v/challenge" in message and "Cloudflare" in message for message in messages)

    async def test_video_page_without_sources_yields_nothing(self, scraper_server: LocalServer) -> None:
        scraper_server.routes[page_path(1)] = page(video("/v/1"))
        scraper_server.routes[page_path(2)] = page("")
        scraper_server.routes["/v/1"] = page("<p>no source</p>")

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            assert await scraper.fetch_urls() == []

    async def test_video_links_with_identical_thumbnails_on_different_pages_are_not_repeated(
        self, scraper_server: LocalServer
    ) -> None:
        same_thumbnail = '<img src="/icons/icon-play.png">'
        scraper_server.routes[page_path(1)] = page(f'<a href="/v/1">{same_thumbnail}</a>')
        scraper_server.routes[page_path(2)] = page(f'<a href="/v/2">{same_thumbnail}</a>')
        scraper_server.routes[page_path(3)] = page("")
        scraper_server.routes["/v/1"] = source("/media/1.mp4")
        scraper_server.routes["/v/2"] = source("/media/2.mp4")

        async with FappeloScraper("https://fapello.com/model/") as scraper:
            urls = await scraper.fetch_urls()

        assert urls == [scraper_server.url("/media/1.mp4"), scraper_server.url("/media/2.mp4")]
        assert scraper_server.count("/v/1") == 1
        assert scraper_server.count("/v/2") == 1

    @pytest.mark.parametrize(
        "error",
        [
            HTTPError("server error"),
            ChunkedEncodingError("incomplete response"),
            NiquestsConnectionError("offline"),
            Timeout("request timed out"),
            TimeoutError("request timed out"),
        ],
    )
    async def test_expected_http_and_network_failures_are_skipped(
        self, caplog: pytest.LogCaptureFixture, error: Exception
    ) -> None:
        scraper = FappeloScraper("https://fapello.com/model/")

        async def fail_network(_: str) -> list[str]:
            raise error

        scraper.fetch_video_urls = fail_network  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING, logger="utils.scrapers"):
            assert await scraper.fetch_video_urls_safely("https://h/video") == []

        assert str(error) in caplog.text

    async def test_unexpected_programming_error_propagates(self) -> None:
        scraper = FappeloScraper("https://fapello.com/model/")

        async def fail_programming(_: str) -> list[str]:
            raise ValueError("bug")

        scraper.fetch_video_urls = fail_programming  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="bug"):
            await scraper.fetch_video_urls_safely("https://h/video")
