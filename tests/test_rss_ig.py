from datetime import UTC
from html import unescape
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

import rss_ig as ig


def media(code, timestamp, caption=None):
    return {"shortcode_media": {
        "shortcode": code,
        "taken_at_timestamp": timestamp,
        "owner": {"username": ig.ACCOUNT},
        "__typename": "GraphImage",
        "edge_media_to_caption": {"edges": [] if caption is None else [
            {"node": {"text": caption}}
        ]},
    }}


def embed(*posts, username=ig.ACCOUNT):
    context = {"context": {"username": username, "graphql_media": list(posts)}}
    data = {"require": [
        ["PolarisEmbedSimple", "init", [], [{
            "isProfileEmbed": True,
            "contextJSON": json.dumps(context),
        }]],
    ]}
    return f"<script>var s=(new ServerJS());s.handle({json.dumps(data)});</script>"


class FeedTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "feed.xml"

    def test_repeated_and_overlapping_windows(self):
        first = ig.parse_embed(embed(
            media("Abc-1", 1795082279, "Texto de la publicación"),
            media("Xyz_2", 1795077207),
        ))
        self.assertEqual(ig.update_feed(self.path, first), (2, 2, 0))
        initial = self.path.read_bytes()
        self.assertEqual(ig.update_feed(self.path, first), (0, 2, 0))
        self.assertEqual(self.path.read_bytes(), initial)

        second = ig.parse_embed(embed(
            media("new3", 1795082280, "Una nueva publicación"),
            media("Abc-1", 1795082279, "Texto de la publicación"),
            media("new3", 1795082280),
        ))
        self.assertEqual(ig.update_feed(self.path, second), (1, 3, 0))
        posts = ig.parse_feed(self.path.read_bytes())
        self.assertEqual(list(posts), ["new3", "Abc-1", "Xyz_2"])
        self.assertEqual(posts["Abc-1"], first["Abc-1"].post)
        self.assertEqual(posts["Xyz_2"], first["Xyz_2"].post)

    def test_rss_fields_and_full_caption(self):
        caption = "<evento> & música 🎶\n\n" + " ".join(f"palabra{i}" for i in range(25))
        posts = ig.parse_embed(embed(
            media("Yep", 1795082279, caption),
            media("Nah", 1795077207),
        ))
        ig.update_feed(self.path, posts)
        parsed = ig.parse_feed(self.path.read_bytes())
        self.assertTrue(parsed["Yep"].description.startswith("&lt;evento&gt; &amp;"))
        self.assertEqual(unescape(parsed["Yep"].description.replace("<br>", "\n")), caption)
        self.assertEqual(parsed["Yep"].published.tzinfo, UTC)
        self.assertEqual(parsed["Yep"].published.timestamp(), 1795082279)
        self.assertEqual(parsed["Nah"].description, "Sin texto disponible en Instagram.")
        item = ET.parse(self.path).find("./channel/item")
        self.assertEqual(item.findtext("link"), "https://www.instagram.com/p/Yep/")
        self.assertEqual(item.findtext("guid"), item.findtext("link"))

    def test_backfills_previous_caption_without_changing_identity(self):
        first = ig.parse_embed(embed(media("Old", 1795082279, "Texto anterior…")))
        ig.update_feed(self.path, first)
        original = ig.parse_feed(self.path.read_bytes())["Old"]

        caption = "Primera línea\r\n\r\nSegunda línea\nTercera línea y cierre"
        current = ig.parse_embed(embed(media("Old", 1795082279, caption)))
        self.assertEqual(ig.update_feed(self.path, current), (0, 1, 0))
        item = ET.parse(self.path).find("./channel/item")
        self.assertEqual(item.findtext("guid"), original.link)
        self.assertEqual(item.findtext("title"), original.title)
        self.assertEqual(item.findtext("description"), "Primera línea<br><br>Segunda línea<br>Tercera línea y cierre")

        updated = self.path.read_bytes()
        without_caption = ig.parse_embed(embed(media("Old", 1795082279)))
        ig.update_feed(self.path, without_caption)
        self.assertEqual(self.path.read_bytes(), updated)

    def test_probe_leaves_the_feed_untouched(self):
        with patch.object(ig, "fetch_embed", return_value=embed(media("Yep", 1795082279))):
            ig.run(self.path, probe=True)
        self.assertFalse(self.path.exists())

    def test_backfill_uses_first_carousel_image_and_keeps_guid(self):
        post = media("Old", 1795082279)
        ig.update_feed(self.path, ig.parse_embed(embed(post)))
        original = ig.parse_feed(self.path.read_bytes())["Old"]
        node = post["shortcode_media"]
        node["__typename"] = "GraphSidecar"
        node["display_url"] = "https://a.cdninstagram.com/parent"
        preferred = "https://a.cdninstagram.com/first-640"
        node["edge_sidecar_to_children"] = {"edges": [
            {"node": {"display_resources": [
                {"config_width": 1080, "src": "https://a.cdninstagram.com/first-1080"},
                {"config_width": 640, "src": preferred},
            ]}},
            {"node": {"display_url": "https://a.cdninstagram.com/second"}},
        ]}
        recent = ig.parse_embed(embed(post))
        image_bytes = b"\xff\xd8\xffimagen"
        with patch.object(ig, "fetch_cover", return_value=("jpg", image_bytes)) as fetch:
            self.assertEqual(ig.update_feed(self.path, recent), (0, 1, 1))
            self.assertEqual(ig.update_feed(self.path, recent), (0, 1, 0))
        fetch.assert_called_once_with(preferred)

        item = ET.parse(self.path).find("./channel/item")
        image_url = ig.MEDIA_URL + "Old.jpg"
        self.assertEqual(item.findtext("guid"), original.link)
        self.assertIn(f'<img src="{image_url}"', item.findtext("description"))
        self.assertEqual(item.find(ig.MEDIA_CONTENT).attrib, {
            "url": image_url, "type": "image/jpeg", "medium": "image",
        })
        self.assertEqual((self.path.parent / "media" / "Old.jpg").read_bytes(), image_bytes)
        saved = ig.parse_feed(self.path.read_bytes())
        self.assertEqual(saved["Old"].title, original.title)
        self.assertEqual(saved["Old"].published, original.published)
        ig.validate_cover_files(self.path, saved)

    def test_unavailable_images_keep_the_previous_feed(self):
        previous = ig.parse_embed(embed(media("Old", 1795082279)))
        ig.update_feed(self.path, previous)
        original = self.path.read_bytes()
        new = media("New", 1795082280)
        new["shortcode_media"]["display_url"] = "https://a.cdninstagram.com/new"
        with patch.object(ig, "fetch_cover", side_effect=URLError("sin conexión")):
            with self.assertRaises(URLError):
                ig.update_feed(self.path, ig.parse_embed(embed(new)))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.path.parent / "media").exists())

    def test_cover_rejects_external_hosts_and_non_images(self):
        with patch.object(ig, "build_opener") as opener:
            with self.assertRaises(ValueError):
                ig.fetch_cover("https://a.cdninstagram.com.example.com/malicioso")
            opener.assert_not_called()
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers.get_content_type.return_value = "image/jpeg"
            response.read.return_value = b"<html>En lugar de la portada</html>"
            with self.assertRaises(ValueError):
                ig.fetch_cover("https://a.cdninstagram.com/imagen")

    def test_failed_updates_keep_previous_bytes(self):
        initial = ig.parse_embed(embed(media("Old", 1795082279)))
        ig.update_feed(self.path, initial)
        original = self.path.read_bytes()
        bad_source = media("New", 1795082280)
        del bad_source["shortcode_media"]["taken_at_timestamp"]
        bad_responses = [
            "<html><title>Log in to Instagram</title></html>",
            embed(),
            embed(media("New", 1795082280), bad_source),
            embed(media("New", 1795082280), username="otra_cuenta"),
            embed(media("New", 1795082280, "Un carácter inválido \x01")),
        ]
        for response in bad_responses:
            with self.subTest(response=response), patch.object(ig, "fetch_embed", return_value=response):
                with self.assertRaises((ValueError, KeyError, ET.ParseError)):
                    ig.run(self.path)
                self.assertEqual(self.path.read_bytes(), original)
        errors = [
            URLError("sin conexión"),
            HTTPError(ig.EMBED_URL, 429, "Too Many Requests", None, None),
        ]
        for error in errors:
            with self.subTest(error=error), patch.object(ig, "urlopen", side_effect=error):
                with self.assertRaises(URLError):
                    ig.run(self.path)
                self.assertEqual(self.path.read_bytes(), original)

        with patch.object(ig, "urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.status = 200
            response.url = "https://www.instagram.com/accounts/login/"
            with self.assertRaises(ValueError):
                ig.run(self.path)
            self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
