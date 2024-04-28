import asyncio
from pathlib import Path

import aiofiles
import aiohttp
from tqdm import tqdm

FILE_FORMATS = {
    "Images": {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".jpe",
        ".svg",
        ".tif",
        ".tiff",
        ".jif",
    },
    "Videos": {
        ".mpeg",
        ".avchd",
        ".webm",
        ".mpv",
        ".swf",
        ".avi",
        ".m4p",
        ".wmv",
        ".mp2",
        ".m4v",
        ".qt",
        ".mpe",
        ".mp4",
        ".flv",
        ".mov",
        ".mpg",
        ".ogg",
    },
}


def media_seperation(path: Path) -> None:

    image_files = [
        filename
        for filename in path.iterdir()
        if filename.suffix in FILE_FORMATS["Images"]
    ]
    video_files = [
        video_file
        for video_file in path.iterdir()
        if video_file.suffix in FILE_FORMATS["Videos"]
    ]

    images_folder = Path(path / "Images")
    images_folder.mkdir(exist_ok=True)
    videos_folder = Path(path / "Videos")
    videos_folder.mkdir(exist_ok=True)

    for image in image_files:
        image.rename(images_folder / image.name)

    for video in video_files:
        video.rename(videos_folder / video.name)


class Downloader:
    def __init__(self, urls: list[str], folder: Path) -> None:
        self.urls = urls
        self.folder = folder

    async def download_file(
        self, session: aiohttp.ClientSession, url: str
    ) -> bytes | None:
        response = await session.get(url)
        total = int(response.headers.get("Content-Length", 0))
        downloaded = bytearray()
        with tqdm(
            total=total, unit_scale=True, unit="B", leave=False, desc=url, disable=True
        ) as progress:
            async for chunk, _ in response.content.iter_chunks():
                progress.update(len(chunk))
                downloaded.extend(chunk)
        return downloaded

    async def store_file(self, data: bytes, filename: str) -> None:
        async with aiofiles.open(self.folder / filename, mode="wb") as f:
            await f.write(data)

    async def download_and_store(
        self, session: aiohttp.ClientSession, url: str
    ) -> None:
        data = await self.download_file(session, url)
        if not data:
            return
        filename = url.split("/")[-1]
        await self.store_file(data, filename)

    async def download_all(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)

        async with aiohttp.ClientSession() as session:
            coros = [self.download_and_store(session, link) for link in self.urls]
            for func in tqdm(
                asyncio.as_completed(coros), total=len(coros), desc="Processing"
            ):
                await func
