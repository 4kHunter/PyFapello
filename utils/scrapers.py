import asyncio
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from .database import Database


class FappeloScraper:
    BASE_URL: str = "https://fapello.com/ajax/model/{name}/"

    def __init__(self, link: str) -> None:
        self.link = link

    def get_model_name(self) -> str:
        return urlparse(self.link).path

    def get_soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    async def get_response(self, url: str) -> str:
        sem = asyncio.Semaphore(10)
        async with sem, aiohttp.ClientSession() as session, session.get(
            url
        ) as response:
            return await response.text()

    async def fetch_urls(self) -> list[str]:
        video_urls: list[str] = []
        image_urls: list[str] = []
        page = 1

        while True:
            model_name = self.get_model_name()
            ajax_url = f"{self.BASE_URL.format(name=model_name)}page-{page}/"
            response = await self.get_response(ajax_url)

            if not response:
                break

            soup = self.get_soup(response)

            image_anchors = {
                img["src"].replace("_300px", "")
                for img in soup.findAll("img")
                if "icon-play" not in img["src"]
            }

            image_urls.extend(list(image_anchors))

            icon_anchors = {
                anchors["href"]
                for anchors in soup.findAll("a", href=True)
                for img in anchors.findAll("img")
                if "icon-play" in img["src"]
            }

            if icon_anchors:
                response_videos = await asyncio.gather(
                    *[self.get_response(url) for url in icon_anchors]
                )
                video_soups = [self.get_soup(response) for response in response_videos]

                video_anchors = {
                    video["src"]
                    for video_soup in video_soups
                    for video in video_soup.findAll("source")
                }

                video_urls.extend(list(video_anchors))

            page += 1

        return image_urls + video_urls

    async def save_to_database(self) -> list[str]:
        urls = await self.fetch_urls()
        if not urls:
            return []
        with Database() as db:
            existing_urls = db.get_existing_urls(urls)
            non_existing_urls = [url for url in urls if url not in existing_urls]
            for filename in non_existing_urls:
                db.insert(filename)
        return non_existing_urls
