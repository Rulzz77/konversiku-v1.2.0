import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app import (
    _image_urls_from_item,
    _is_allowed_tiktok_image_url,
    _find_tiktok_item,
    _job_progress_hook,
    DOWNLOAD_JOBS,
    resolve_tiktok_photo_post,
    tiktok_photo_id,
)


class TikTokSlideshowResolverTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://www.tiktok.com/@sazporto/photo/7699863357045656853?is_from_webapp=1"

    def test_extracts_photo_post_id(self):
        self.assertEqual(tiktok_photo_id(self.url), "7699863357045656853")

    def test_extracts_image_urls_from_item_struct(self):
        item = {
            "imagePost": {
                "images": [
                    {"imageURL": {"urlList": ["https://p16-sign.tiktokcdn-us.com/a.jpeg", "https://p16-sign.tiktokcdn-us.com/b.jpeg"]}},
                    {"displayImage": {"urlList": ["https://p16-sign-va.tiktokcdn.com/c.jpeg"]}},
                ]
            }
        }
        self.assertEqual(
            _image_urls_from_item(item),
            ["https://p16-sign.tiktokcdn-us.com/a.jpeg", "https://p16-sign-va.tiktokcdn.com/c.jpeg"],
        )

    def test_supports_legacy_image_post_info_shape(self):
        item = {"id": "7699863357045656853", "image_post_info": {"images": [
            {"image_url": {"url_list": ["https://p16-sign.tiktokcdn-us.com/photo.png"]}}
        ]}}
        self.assertEqual(_image_urls_from_item(item), ["https://p16-sign.tiktokcdn-us.com/photo.png"])
        self.assertIs(_find_tiktok_item({"item": item}, "7699863357045656853"), item)

    def test_download_progress_hook_updates_percentage(self):
        DOWNLOAD_JOBS["unit-progress"] = {"id": "unit-progress", "percent": 0, "status": "running"}
        hook = _job_progress_hook("unit-progress", start=0, span=100, label="Testing")
        hook({"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100, "speed": 1048576, "eta": 3})
        self.assertEqual(DOWNLOAD_JOBS["unit-progress"]["percent"], 25)
        self.assertIn("sisa sekitar 3 dtk", DOWNLOAD_JOBS["unit-progress"]["text"])
        DOWNLOAD_JOBS.pop("unit-progress", None)

    def test_rejects_non_tiktok_image_hosts(self):
        item = {"imagePost": {"images": [{"imageURL": {"urlList": ["https://not-tiktok.example/pic.jpg"]}}]}}
        self.assertEqual(_image_urls_from_item(item), [])
        self.assertFalse(_is_allowed_tiktok_image_url("https://example.com/pic.jpg"))

    @patch("app.requests.get")
    def test_resolves_public_item_detail_response(self, mock_get):
        payload = {"itemInfo": {"itemStruct": {
            "id": "7699863357045656853", "desc": "Photo slideshow",
            "author": {"uniqueId": "sazporto"},
            "imagePost": {"images": [
                {"imageURL": {"urlList": ["https://p16-sign.tiktokcdn-us.com/a.jpeg"]}},
                {"imageURL": {"urlList": ["https://p16-sign.tiktokcdn-us.com/b.jpeg"]}},
            ]},
        }}}
        mock_get.return_value = SimpleNamespace(ok=True, status_code=200, json=lambda: payload)
        details = resolve_tiktok_photo_post(self.url)
        self.assertEqual(details["title"], "Photo slideshow")
        self.assertEqual(details["uploader"], "sazporto")
        self.assertEqual(len(details["images"]), 2)
        mock_get.assert_called_once()

    def test_only_https_cdn_is_allowed(self):
        self.assertTrue(_is_allowed_tiktok_image_url("https://p16-sign.tiktokcdn-us.com/a.jpeg"))
        self.assertFalse(_is_allowed_tiktok_image_url("http://p16-sign.tiktokcdn-us.com/a.jpeg"))


if __name__ == "__main__":
    unittest.main()
