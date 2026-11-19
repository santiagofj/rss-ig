"""Actualiza el RSS a partir del embed público de Instagram."""

import argparse
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from html import escape
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen
import xml.etree.ElementTree as ET


ACCOUNT = "laagendaculturaldefacu"
PROFILE_URL = f"https://www.instagram.com/{ACCOUNT}/"
EMBED_URL = f"{PROFILE_URL}embed/"
POST_URL = "https://www.instagram.com/p/"
FEED_PATH = Path(__file__).with_name("feed.xml")
CHANNEL_TITLE = f"@{ACCOUNT} en Instagram"
CHANNEL_DESCRIPTION = f"Publicaciones recientes de @{ACCOUNT}."
NO_CAPTION = "Sin texto disponible en Instagram."
SHORTCODE = re.compile(r"[A-Za-z0-9_-]+")
SERVER_DATA = re.compile(r"new ServerJS\(\)\);s\.handle\(\s*(?=\{)")
MAX_EMBED_BYTES = 3_000_000
MAX_IMAGE_BYTES = 1_000_000
IMAGE_TYPES = {"jpg": "image/jpeg", "webp": "image/webp", "png": "image/png"}
MEDIA_DIRECTORY = "media"
MEDIA_URL = "https://santiagofj.github.io/rss-ig/media/"
MEDIA_NAMESPACE = "http://search.yahoo.com/mrss/"
MEDIA_CONTENT = f"{{{MEDIA_NAMESPACE}}}content"
ET.register_namespace("media", MEDIA_NAMESPACE)


@dataclass(frozen=True)
class Post:
    code: str
    published: datetime
    title: str
    description: str
    cover_extension: str | None = None

    @property
    def link(self) -> str:
        return f"{POST_URL}{self.code}/"

    @property
    def cover_filename(self) -> str | None:
        if self.cover_extension is None:
            return None
        return f"{self.code}.{self.cover_extension}"


@dataclass(frozen=True)
class RecentPost:
    post: Post
    cover_source: str | None


class NoImageRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_embed() -> str:
    with urlopen(EMBED_URL, timeout=25) as response:
        if response.status != 200 or response.url != EMBED_URL:
            raise ValueError("Instagram devolvió un estado o una URL inesperados")
        body = response.read(MAX_EMBED_BYTES + 1)
    if len(body) > MAX_EMBED_BYTES:
        raise ValueError("El embed excede el límite de tamaño")
    return body.decode("utf-8")


def cover_source(media: dict) -> str | None:
    children = media.get("edge_sidecar_to_children", {}).get("edges", [])
    if children:
        media = children[0]["node"]
    resources = media.get("display_resources", [])
    if resources:
        return min(resources, key=lambda image: abs(image["config_width"] - 640))["src"]
    return media.get("display_url")


def image_extension(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return None


def fetch_cover(url: str) -> tuple[str, bytes]:
    source = urlsplit(url)
    host = source.hostname or ""
    if (
        source.scheme != "https"
        or not host.endswith((".cdninstagram.com", ".fbcdn.net"))
        or source.netloc != host
    ):
        raise ValueError("La imagen no viene de un CDN público de Instagram")
    with build_opener(NoImageRedirects()).open(url, timeout=15) as response:
        if response.status != 200:
            raise ValueError("Instagram devolvió un estado inesperado para la imagen")
        mime_type = response.headers.get_content_type()
        content = response.read(MAX_IMAGE_BYTES + 1)
    extension = image_extension(content)
    if extension is None or len(content) > MAX_IMAGE_BYTES or IMAGE_TYPES[extension] != mime_type:
        raise ValueError("La imagen tiene un tamaño o formato inesperado")
    return extension, content


def post_from_media(media: dict) -> Post:
    code = media["shortcode"]
    timestamp = media["taken_at_timestamp"]
    if not SHORTCODE.fullmatch(code) or media["owner"]["username"] != ACCOUNT:
        raise ValueError("El embed contiene una publicación de identidad inesperada")
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("El embed contiene una fecha de publicación inválida")

    edges = media["edge_media_to_caption"]["edges"]
    if not isinstance(edges, list):
        raise ValueError("El embed contiene un formato de texto inesperado")
    caption = "\n".join(edge["node"]["text"] for edge in edges).strip()
    if caption:
        words = caption.split()
        title = " ".join(words[:8]) + ("…" if len(words) > 8 else "")
        # La descripción RSS se interpreta como HTML en lectores de feeds.
        caption = caption.replace("\r\n", "\n").replace("\r", "\n")
        description = escape(caption, quote=False).replace("\n", "<br>")
    else:
        kind = "Carrusel" if media["__typename"] == "GraphSidecar" else "Publicación"
        title = f"{kind} de @{ACCOUNT}"
        description = NO_CAPTION
    return Post(code, datetime.fromtimestamp(timestamp, UTC), title, description)


def parse_embed(page: str) -> dict[str, RecentPost]:
    marker = SERVER_DATA.search(page)
    if marker is None:
        raise ValueError("El HTML no contiene los datos esperados del embed")
    data, _ = json.JSONDecoder().raw_decode(page, marker.end())
    contexts = []
    for entry in data["require"]:
        if entry[:2] == ["PolarisEmbedSimple", "init"]:
            contexts.extend(arg for arg in entry[3] if arg.get("isProfileEmbed") is True)
    if len(contexts) != 1:
        raise ValueError("El HTML no contiene un contexto único de perfil")
    context = json.loads(contexts[0]["contextJSON"])["context"]
    if context["username"] != ACCOUNT:
        raise ValueError("El embed corresponde a otra cuenta")
    media = context["graphql_media"]
    if not isinstance(media, list) or not media:
        raise ValueError("El embed no contiene publicaciones")

    posts = {}
    for entry in media:
        node = entry["shortcode_media"]
        post = post_from_media(node)
        posts.setdefault(post.code, RecentPost(post, cover_source(node)))
    return posts


def cover_html(url: str) -> str:
    return f'<p><img src="{url}" alt="Imagen de la publicación"></p>'


def parse_feed(content: bytes) -> dict[str, Post]:
    rss = ET.fromstring(content)
    channels = rss.findall("channel")
    if rss.tag != "rss" or rss.get("version") != "2.0" or len(channels) != 1:
        raise ValueError("El archivo no tiene un canal RSS 2.0")
    channel = channels[0]
    if (
        channel.findtext("link") != PROFILE_URL
        or not channel.findtext("title")
        or not channel.findtext("description")
    ):
        raise ValueError("El canal RSS no corresponde al perfil esperado")

    posts = {}
    previous_date = None
    for item in channel.findall("item"):
        link = item.findtext("link", "")
        code = link.removeprefix(POST_URL).removesuffix("/")
        guid = item.find("guid")
        title = item.findtext("title")
        description = item.findtext("description")
        if (
            not SHORTCODE.fullmatch(code)
            or link != f"{POST_URL}{code}/"
            or code in posts
            or guid is None
            or guid.get("isPermaLink") != "true"
            or guid.text != link
            or not title
            or not description
        ):
            raise ValueError("El RSS contiene una entrada inválida o duplicada")
        published = parsedate_to_datetime(item.findtext("pubDate", ""))
        if published.tzinfo is None or (previous_date is not None and published > previous_date):
            raise ValueError("El RSS contiene fechas sin zona horaria o fuera de orden")

        extension = None
        cover = item.find(MEDIA_CONTENT)
        if cover is not None:
            url = cover.get("url", "")
            extension = url.rsplit(".", 1)[-1]
            if (
                extension not in IMAGE_TYPES
                or url != f"{MEDIA_URL}{code}.{extension}"
                or cover.get("type") != IMAGE_TYPES[extension]
                or cover.get("medium") != "image"
                or not description.startswith(cover_html(url))
            ):
                raise ValueError("El RSS contiene una referencia de imagen inválida")
            description = description.removeprefix(cover_html(url))
            if not description:
                raise ValueError("Falta la descripción de una publicación")
        posts[code] = Post(code, published, title, description, extension)
        previous_date = published
    if not posts:
        raise ValueError("El RSS no contiene publicaciones")
    return posts


def render_feed(posts: dict[str, Post]) -> bytes:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = CHANNEL_TITLE
    ET.SubElement(channel, "link").text = PROFILE_URL
    ET.SubElement(channel, "description").text = CHANNEL_DESCRIPTION
    ET.SubElement(channel, "language").text = "es"

    for post in sorted(posts.values(), key=lambda post: (post.published, post.code), reverse=True):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = post.title
        description = post.description
        if post.cover_extension:
            url = f"{MEDIA_URL}{post.cover_filename}"
            description = cover_html(url) + description
            ET.SubElement(
                item, MEDIA_CONTENT, url=url, type=IMAGE_TYPES[post.cover_extension], medium="image"
            )
        ET.SubElement(item, "description").text = description
        ET.SubElement(item, "link").text = post.link
        ET.SubElement(item, "guid", isPermaLink="true").text = post.link
        ET.SubElement(item, "pubDate").text = format_datetime(post.published.astimezone(UTC), usegmt=True)
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True) + b"\n"


def validated_feed(posts: dict[str, Post]) -> bytes:
    content = render_feed(posts)
    if parse_feed(content) != posts:
        raise ValueError("El RSS generado difiere de las publicaciones que debe contener")
    return content


def replace_file(path: Path, content: bytes) -> None:
    # El temporal reside junto al destino para que el reemplazo sea atómico.
    with NamedTemporaryFile(dir=path.parent, prefix=".feed-", delete=False) as output:
        candidate = Path(output.name)
    try:
        candidate.write_bytes(content)
        candidate.replace(path)
    finally:
        candidate.unlink(missing_ok=True)


def merge_posts(
    previous: dict[str, Post], recent: dict[str, RecentPost]
) -> tuple[dict[str, Post], dict[str, bytes]]:
    # Conservar la identidad y fecha originales de las entradas ya publicadas.
    merged = {code: item.post for code, item in recent.items()} | previous
    covers = {}
    for code, item in recent.items():
        post = merged[code]
        if item.post.description != NO_CAPTION:
            post = replace(post, description=item.post.description)
        if post.cover_extension is None and item.cover_source:
            extension, data = fetch_cover(item.cover_source)
            post = replace(post, cover_extension=extension)
            covers[f"{code}.{extension}"] = data
        merged[code] = post
    return merged, covers


def validate_cover_files(path: Path, posts: dict[str, Post]) -> None:
    for post in posts.values():
        filename = post.cover_filename
        if filename:
            picture = path.parent / MEDIA_DIRECTORY / filename
            if not picture.is_file():
                raise ValueError(f"Falta la imagen guardada de {post.code}")


def update_feed(path: Path, recent: dict[str, RecentPost]) -> tuple[int, int, int]:
    try:
        previous = parse_feed(path.read_bytes())
    except FileNotFoundError:
        previous = {}
    validate_cover_files(path, previous)
    merged, covers = merge_posts(previous, recent)
    added = len(merged) - len(previous)
    if merged != previous:
        content = validated_feed(merged)
        if covers:
            directory = path.parent / MEDIA_DIRECTORY
            directory.mkdir(exist_ok=True)
            for name, data in covers.items():
                replace_file(directory / name, data)
        replace_file(path, content)
    return added, len(merged), len(covers)


def run(path: Path, *, probe: bool = False, validate: bool = False) -> None:
    if validate:
        posts = parse_feed(path.read_bytes())
        validate_cover_files(path, posts)
        pictures = sum(post.cover_extension is not None for post in posts.values())
        print(f"RSS válido con {len(posts)} publicaciones y {pictures} imágenes.")
        return

    recent = parse_embed(fetch_embed())
    print(f"Instagram devolvió {len(recent)} publicaciones válidas sin sesión.")
    if probe:
        posts, covers = merge_posts({}, recent)
        validated_feed(posts)
        print(f"Instagram devolvió {len(covers)} imágenes verificadas.")
        return

    added, total, pictures = update_feed(path, recent)
    print(f"{added} publicaciones nuevas, {total} en el RSS y {pictures} imágenes agregadas.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--probar", action="store_true", help="Consultar el embed y validar un RSS en memoria")
    commands.add_argument("--validar", action="store_true", help="Validar el feed guardado")
    args = parser.parse_args()
    try:
        run(FEED_PATH, probe=args.probar, validate=args.validar)
    except (OSError, ValueError, ET.ParseError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as error:
        print(f"Error al procesar el RSS. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
