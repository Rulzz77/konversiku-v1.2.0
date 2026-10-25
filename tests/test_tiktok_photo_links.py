import unittest

from app import is_tiktok_photo_url, tiktok_photo_audio_url


class TikTokPhotoLinkTests(unittest.TestCase):
    def test_recognizes_photo_post(self):
        url = "https://www.tiktok.com/@sazporto/photo/7699863357045656853?is_from_webapp=1&sender_device=pc"
        self.assertTrue(is_tiktok_photo_url(url))

    def test_rewrites_photo_path_and_preserves_query(self):
        url = "https://www.tiktok.com/@sazporto/photo/7699863357045656853?is_from_webapp=1&sender_device=pc"
        self.assertEqual(
            tiktok_photo_audio_url(url),
            "https://www.tiktok.com/@sazporto/video/7699863357045656853?is_from_webapp=1&sender_device=pc",
        )

    def test_video_link_is_not_modified(self):
        url = "https://www.tiktok.com/@sazporto/video/7699863357045656853"
        self.assertFalse(is_tiktok_photo_url(url))
        self.assertEqual(tiktok_photo_audio_url(url), url)

    def test_non_tiktok_host_is_not_recognized(self):
        url = "https://example.com/@sazporto/photo/7699863357045656853"
        self.assertFalse(is_tiktok_photo_url(url))
        self.assertEqual(tiktok_photo_audio_url(url), url)


if __name__ == "__main__":
    unittest.main()
