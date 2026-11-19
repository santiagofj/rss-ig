"""Actualiza el RSS a partir del embed público de Instagram."""

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from html import escape
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from urllib.request import urlopen
import xml.etree.ElementTree as ET


ACCOUNT = "laagendaculturaldefacu"
PROFILE_URL = f"https://www.instagram.com/{ACCOUNT}/"
EMBED_URL = f"{PROFILE_URL}embed/"
POST_URL = "https://www.instagram.com/p/"
FEED_PATH = Path(__file__).with_name("feed.xml")
CHANNEL_TITLE = f"@{ACCOUNT} en Instagram"
CHANNEL_DESCRIPTION = f"Publicaciones recientes de @{ACCOUNT}."
SHORTCODE = re.compile(r"[A-Za-z0-9_-]+")
SERVER_DATA = re.compile(r"new ServerJS\(\)\);s\.handle\(\s*(?=\{)")
MAX_EMBED_BYTES = 3_000_000


@dataclass(frozen=True)
class Post:
    code: str
    published: datetime
    title: str
    description: str

    @property
    def link(self) -> str:
        return f"{POST_URL}{self.code}/"


def fetch_embed() -> str:
    with urlopen(EMBED_URL, timeout=25) as response:
        if response.status != 200 or response.url != EMBED_URL:
            raise ValueError("Instagram devolvió un estado o una URL inesperados")
        body = response.read(MAX_EMBED_BYTES + 1)
    if len(body) > MAX_EMBED_BYTES:
        raise ValueError("El embed excede el límite de tamaño")
    return body.decode("utf-8")


def excerpt(words: list[str], limit: int) -> str:
    return " ".join(words[:limit]) + ("…" if len(words) > limit else "")


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
    words = " ".join(edge["node"]["text"] for edge in edges).split()
    if words:
        title = excerpt(words, 8)
        # La descripción RSS se interpreta como HTML en lectores de feeds.
        description = escape(excerpt(words, 20), quote=False)
    else:
        kind = "Carrusel" if media["__typename"] == "GraphSidecar" else "Publicación"
        title = f"{kind} de @{ACCOUNT}"
        description = "Sin texto disponible en Instagram."
    return Post(code, datetime.fromtimestamp(timestamp, UTC), title, description)


def parse_embed(page: str) -> dict[str, Post]:
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
        post = post_from_media(entry["shortcode_media"])
        posts.setdefault(post.code, post)
    return posts


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
        posts[code] = Post(code, published, title, description)
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
        ET.SubElement(item, "description").text = post.description
        ET.SubElement(item, "link").text = post.link
        ET.SubElement(item, "guid", isPermaLink="true").text = post.link
        ET.SubElement(item, "pubDate").text = format_datetime(post.published.astimezone(UTC), usegmt=True)
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True) + b"\n"


def replace_feed(path: Path, posts: dict[str, Post]) -> None:
    content = render_feed(posts)
    if parse_feed(content) != posts:
        raise ValueError("El RSS generado difiere de las publicaciones que debe contener")

    # El temporal reside junto al RSS para que el reemplazo sea atómico.
    with NamedTemporaryFile(dir=path.parent, prefix=".feed-", delete=False) as output:
        candidate = Path(output.name)
    try:
        candidate.write_bytes(content)
        candidate.replace(path)
    finally:
        candidate.unlink(missing_ok=True)


def update_feed(path: Path, recent: dict[str, Post]) -> tuple[int, int]:
    try:
        previous = parse_feed(path.read_bytes())
    except FileNotFoundError:
        previous = {}
    # Conservar los datos que ya se entregaron cuando la ventana se superpone.
    merged = recent | previous
    added = len(merged) - len(previous)
    if added:
        replace_feed(path, merged)
    return added, len(merged)


def run(path: Path, *, probe: bool = False, validate: bool = False) -> None:
    if validate:
        posts = parse_feed(path.read_bytes())
        print(f"RSS válido con {len(posts)} publicaciones.")
        return

    recent = parse_embed(fetch_embed())
    print(f"Instagram devolvió {len(recent)} publicaciones válidas sin sesión.")
    if probe:
        if parse_feed(render_feed(recent)) != recent:
            raise ValueError("El embed no produjo un RSS válido")
        return

    added, total = update_feed(path, recent)
    print(f"{added} publicaciones nuevas y {total} en el RSS.")


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
