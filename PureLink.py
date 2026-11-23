# Name: PureLink
# Author: @GeassMod
# Version: 1.3.0
# Description: Removes tracking parameters from links and expands short URLs.
# requires: aiohttp

__version__ = (1, 3, 0)

# meta developer: @GeassMod

import asyncio
import html
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from .. import loader, utils
from herokutl.tl.custom import Message


@loader.tds
class PureLinkMod(loader.Module):
    """Cleans links from tracking junk and expands short links."""

    strings = {
        "name": "PureLink",
    }

    strings_ru = {
        "name": "PureLink",
    }

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    URL_RE = re.compile(r"https?://[^\s<>\"'«»]+", re.IGNORECASE)
    DOMAIN_RE = re.compile(
        r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+(/[^\s]*)?$",
        re.IGNORECASE,
    )

    # Params starting with these prefixes are always junk.
    TRACKING_PREFIXES = (
        "utm_",
        "mc_",
        "_hs",
        "mtm_",
        "pk_",
        "vero_",
        "oly_",
        "spm_",
        "pf_rd_",
        "pd_rd_",
        "__twitter",
        "__cft__",
    )

    # Exact junk param names.
    TRACKING_PARAMS = frozenset(
        {
            # Google Ads / Analytics
            "fbclid",
            "gclid",
            "gclsrc",
            "dclid",
            "gbraid",
            "wbraid",
            "gad_source",
            "_ga",
            "_gl",
            # Microsoft / Yandex / Rambler
            "msclkid",
            "yclid",
            "_openstat",
            # Instagram / Facebook
            "igsh",
            "igshid",
            "ig_mid",
            "ig_rid",
            "rdid",
            "mibextid",
            # Share junk
            "share_source",
            "share_medium",
            "share_id",
            "share_app_id",
            "share_link_id",
            "u_code",
            "is_from_webapp",
            "sender_device",
            # Marketing
            "mkt_tok",
            "s_kwcid",
            "spm",
            "guce_ref",
            "guce_referrer",
            "guccounter",
            # AliExpress affiliate junk
            "aff_platform",
            "aff_trace_key",
            # Referrer
            "ref_src",
            "ref_url",
        }
    )

    # Junk params that are only junk on specific domains
    # (e.g. ?t= is a timestamp on YouTube, but share-junk on X).
    DOMAIN_PARAMS = {
        "youtube.com": {"si", "feature", "app"},
        "youtu.be": {"si", "feature", "app"},
        "spotify.com": {"si"},
        "twitter.com": {"s", "t"},
        "x.com": {"s", "t"},
        "tiktok.com": {"_r", "_t", "web_id"},
        "linkedin.com": {"trk", "trkinfo"},
        "lnkd.in": {"trk", "trkinfo"},
    }

    # JS redirects hidden in the page body:
    # window.location.href = "..." / location.replace("...") / etc.
    JS_REDIRECT_RE = re.compile(
        r"(?:window\.)?location"
        r"(?:\.href\s*=|\.replace\(|\.assign\(|\s*=)\s*[\"']"
        r"(https?://[^\"'\s>]+)[\"']",
        re.IGNORECASE,
    )

    # <meta http-equiv="refresh" content="0;url=...">
    META_REFRESH_RE = re.compile(
        r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+"
        r"content=[\"'][^\"']*?url=([\"']?)(https?://[^\"'>\s]+)\1",
        re.IGNORECASE,
    )

    # Max bytes of HTML to scan for hidden redirects.
    HTML_SCAN_LIMIT = 256 * 1024

    # Keyless shortener APIs: service -> endpoint.
    SHORTENER_APIS = {
        "tinyurl": "https://tinyurl.com/api-create.php",
        "clck": "https://clck.ru/--",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "shortener",
                "clck",
                "Shortener service: clck (Yandex) / tinyurl",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "timeout",
                10,
                "HTTP timeout for .unshort (seconds)",
                validator=loader.validators.Integer(
                    minimum=3,
                    maximum=30,
                ),
            ),
            loader.ConfigValue(
                "max_hops",
                10,
                "Max redirects to follow in .unshort",
                validator=loader.validators.Integer(
                    minimum=1,
                    maximum=20,
                ),
            ),
            loader.ConfigValue(
                "clean_result",
                True,
                "Also clean tracking params from the final URL after .unshort",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "html_redirects",
                True,
                "Detect JS / meta-refresh redirects hidden in HTML pages",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "extra_params",
                [],
                "Extra tracking params to strip",
                validator=loader.validators.Series(
                    loader.validators.String()
                ),
            ),
            loader.ConfigValue(
                "keep_params",
                [],
                "Params that should never be stripped",
                validator=loader.validators.Series(
                    loader.validators.String()
                ),
            ),
        )

    # -------------------------
    # HELPERS
    # -------------------------

    @staticmethod
    def _plural(count, one, few, many):
        """Russian pluralization helper."""

        count = abs(count) % 100

        if 11 <= count <= 14:
            return many

        count %= 10

        if count == 1:
            return one

        if 2 <= count <= 4:
            return few

        return many

    def _extract_urls(self, raw):
        """Pull all http(s) URLs out of raw text."""

        urls = [
            url.rstrip(".,!?;:)]}>\"'«»")
            for url in self.URL_RE.findall(raw)
        ]

        urls = [url for url in urls if url]

        if urls:
            # Dedupe, keep the order.
            return list(dict.fromkeys(urls))

        # Allow bare domains like "example.com/path?x=1"
        raw = raw.strip()

        if raw and " " not in raw and self.DOMAIN_RE.match(raw):
            return ["https://" + raw]

        return []

    async def _get_target_urls(self, message):
        """Collect URLs from args or from the replied message."""

        raw = utils.get_args_raw(message)

        if not raw and message.is_reply:
            reply = await message.get_reply_message()
            raw = getattr(reply, "raw_text", "") or ""

        if not raw:
            return []

        return self._extract_urls(raw)

    def _clean_url(self, url):
        """Strip tracking params. Returns (clean_url, removed_params)."""

        try:
            parts = urlsplit(url)
        except ValueError:
            return url, []

        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            return url, []

        host = parts.netloc.lower().removeprefix("www.")

        # Domain-specific junk.
        domain_rules = set()

        for domain, params in self.DOMAIN_PARAMS.items():
            if host == domain or host.endswith("." + domain):
                domain_rules |= params

        extra = {p.lower() for p in self.config["extra_params"]}
        keep = {p.lower() for p in self.config["keep_params"]}

        kept = []
        removed = []

        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            k = key.lower()

            if k in keep:
                kept.append((key, value))
                continue

            is_tracking = (
                k in self.TRACKING_PARAMS
                or k in domain_rules
                or k in extra
                or any(k.startswith(prefix) for prefix in self.TRACKING_PREFIXES)
            )

            if is_tracking:
                removed.append(key)
            else:
                kept.append((key, value))

        if not removed:
            return url, []

        clean = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(kept),
                parts.fragment,
            )
        )

        return clean, removed

    @staticmethod
    def _same_www_mirror(first, second):
        """True if the URLs differ only by the www. prefix in the host."""

        try:
            a = urlsplit(first)
            b = urlsplit(second)
        except ValueError:
            return False

        host_a = a.netloc.lower().removeprefix("www.")
        host_b = b.netloc.lower().removeprefix("www.")

        return (
            a.scheme == b.scheme
            and host_a == host_b
            and a.path == b.path
            and a.query == b.query
            and a.fragment == b.fragment
        )

    async def _shorten_url(self, session, url):
        """Shorten a URL. Returns (short_url, error_message)."""

        service = self.config["shortener"]

        if service not in self.SHORTENER_APIS:
            service = "clck"

        async with session.get(
            self.SHORTENER_APIS[service]
            + "?url="
            + quote(url, safe=""),
        ) as resp:
            status = resp.status
            text = (await resp.text()).strip()

        if text.startswith("http") and len(text) < 100:
            return text, None

        return (
            None,
            "сервис вернул нестандартный ответ (HTTP {}) — "
            "попробуй позже или другой сервис".format(status),
        )

    async def _resolve_url(self, session, url):
        """Follow redirects (HTTP + hidden HTML ones).

        Returns (chain, status, hop_limit).
        """

        chain = [url]
        current = url
        status = 0

        for _ in range(self.config["max_hops"]):
            async with session.get(current, allow_redirects=False) as resp:
                status = resp.status

                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")

                    if not location:
                        return chain, status, False

                    nxt = urljoin(current, location.strip())
                elif (
                    self.config["html_redirects"]
                    and "html" in resp.headers.get("Content-Type", "").lower()
                ):
                    # Page may hide the redirect in JS / meta-refresh
                    # (ad link shorteners love to do this).
                    body = await resp.content.read(self.HTML_SCAN_LIMIT)
                    nxt = self._find_html_redirect(
                        body.decode("utf-8", "ignore")
                    )

                    if not nxt:
                        return chain, status, False

                    nxt = urljoin(current, nxt.strip())
                else:
                    return chain, status, False

                if not nxt.lower().startswith(("http://", "https://")):
                    return chain, status, False

                if nxt == current:
                    return chain, status, False

                if self._same_www_mirror(current, nxt):
                    # Same page, just www/non-www — keep one chain entry.
                    chain[-1] = nxt
                else:
                    chain.append(nxt)

                current = nxt

        return chain, status, True

    @classmethod
    def _find_html_redirect(cls, body):
        """Pull redirect target out of an HTML page.

        Covers <meta http-equiv="refresh"> and the common
        JS patterns (window.location.href / location.replace / etc).
        """

        match = cls.META_REFRESH_RE.search(body)

        if match:
            return match.group(2)

        match = cls.JS_REDIRECT_RE.search(body)

        if match:
            return match.group(1)

        return None

    def _format_chain(self, url, chain, status, hop_limit):
        """Render the .unshort result."""

        if len(chain) == 1:
            if status >= 400:
                return (
                    f"❌ <b>HTTP {status}</b> — сервер ответил ошибкой\n"
                    f"{html.escape(url)}"
                )

            return (
                f"ℹ️ <b>Переадресаций нет</b> (HTTP {status}):\n"
                f"{html.escape(url)}"
            )

        final = chain[-1]
        hops = len(chain) - 1
        word = self._plural(hops, "переход", "перехода", "переходов")

        lines = [
            "🧭 <b>PureLink | Разворот ссылки</b>",
            "",
            f"🔗 {html.escape(final)}",
            "",
            f"♻️ <b>Цепочка ({hops} {word}):</b>",
        ]

        for i, link in enumerate(chain):
            lines.append(f"<code>{i + 1}.</code> {html.escape(link)}")

        if hop_limit:
            lines.append("⚠️ <b>Достигнут лимит переходов.</b>")

        if status >= 400:
            lines.append(f"⚠️ <b>Финальная страница отвечает HTTP {status}.</b>")

        if self.config["clean_result"]:
            clean, removed = self._clean_url(final)

            if removed:
                lines.append("")
                lines.append("🧹 <b>Чистая версия:</b>")
                lines.append(html.escape(clean))

        return "\n".join(lines)

    # -------------------------
    # COMMANDS
    # -------------------------

    @loader.command(ru_doc="Очистить ссылку от трекинг-параметров")
    async def clink(self, message: Message):
        """Remove tracking parameters from links."""

        urls = await self._get_target_urls(message)

        if not urls:
            await utils.answer(
                message,
                "❌ Ссылка не найдена.\n\n"
                "<code>.clink https://example.com/?utm_source=telegram</code>\n"
                "Или ответь командой на сообщение со ссылкой.",
            )
            return

        total = len(urls)
        urls = urls[:10]

        if len(urls) == 1:
            clean, removed = self._clean_url(urls[0])

            if not removed:
                await utils.answer(
                    message,
                    "✅ <b>Ссылка уже чистая!</b>\n\n"
                    + html.escape(urls[0]),
                )
                return

            unique = list(dict.fromkeys(removed))

            text = (
                "🔗 <b>PureLink | Чистая ссылка</b>\n\n"
                + html.escape(clean)
                + "\n\n🗑 <b>Удалено ("
                + str(len(unique))
                + "):</b> <code>"
                + html.escape(", ".join(unique))
                + "</code>"
            )
        else:
            lines = ["🔗 <b>PureLink | Чистые ссылки</b>"]

            for i, url in enumerate(urls, 1):
                clean, removed = self._clean_url(url)

                lines.append("")
                lines.append(f"{i}. {html.escape(clean)}")

                if removed:
                    unique = list(dict.fromkeys(removed))[:8]

                    lines.append(
                        "   🗑 <code>"
                        + html.escape(", ".join(unique))
                        + "</code>"
                    )

            text = "\n".join(lines)

        if total > 10:
            text += "\n\n⚠️ <b>Обработаны первые 10 ссылок.</b>"

        await utils.answer(message, text)

    @loader.command(ru_doc="Сократить ссылку")
    async def short(self, message: Message):
        """Shorten a URL (TinyURL / Yandex clck.ru)."""

        raw = utils.get_args_raw(message)
        urls = []

        if raw:
            urls = self._extract_urls(raw)

            if not urls:
                raw = raw.strip()

                if raw and " " not in raw and self.DOMAIN_RE.match(raw):
                    urls = ["https://" + raw]

        if not urls and message.is_reply:
            reply = await message.get_reply_message()
            urls = self._extract_urls(getattr(reply, "raw_text", "") or "")

        if not urls:
            await utils.answer(
                message,
                "❌ Ссылка не найдена.\n\n"
                "<code>.short https://example.com/very/long/link</code>\n"
                "Или ответь командой на сообщение со ссылкой.",
            )
            return

        urls = urls[:3]
        blocks = []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config["timeout"]),
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "*/*",
            },
        ) as session:
            for url in urls:
                try:
                    short, error = await self._shorten_url(session, url)
                except asyncio.TimeoutError:
                    error = "таймаут"
                    short = None
                except aiohttp.ClientError as e:
                    error = html.escape(str(e))[:120]
                    short = None
                except Exception as e:
                    # Safety net: never crash the whole command.
                    error = html.escape(str(e))[:120] or type(e).__name__
                    short = None

                if short:
                    blocks.append(
                        "✂️ <b>PureLink | Короткая ссылка</b>\n\n"
                        f"🔗 {html.escape(short)}\n"
                        f"📎 <b>Оригинал:</b> {html.escape(url)}"
                    )
                else:
                    blocks.append(
                        f"❌ <b>Не удалось сократить:</b> {html.escape(url)}\n"
                        f"<code>{error}</code>"
                    )

        await utils.answer(message, "\n\n".join(blocks))

    @loader.command(ru_doc="Развернуть короткую ссылку")
    async def unshort(self, message: Message):
        """Expand short URLs and show the redirect chain."""

        urls = await self._get_target_urls(message)

        if not urls:
            await utils.answer(
                message,
                "❌ Ссылка не найдена.\n\n"
                "<code>.unshort https://bit.ly/xxxxxx</code>\n"
                "Или ответь командой на сообщение со ссылкой.",
            )
            return

        urls = urls[:3]

        blocks = []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config["timeout"]),
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "*/*",
            },
        ) as session:
            for url in urls:
                try:
                    chain, status, hop_limit = await self._resolve_url(
                        session,
                        url,
                    )
                except asyncio.TimeoutError:
                    blocks.append(
                        f"⏰ <b>Таймаут:</b> {html.escape(url)}"
                    )
                    continue
                except aiohttp.ClientError as e:
                    error = html.escape(str(e))[:120]
                    blocks.append(
                        f"❌ <b>Ошибка соединения:</b> {html.escape(url)}\n"
                        f"<code>{error}</code>"
                    )
                    continue
                except ValueError:
                    blocks.append(
                        f"❌ <b>Некорректная ссылка:</b> {html.escape(url)}"
                    )
                    continue

                blocks.append(
                    self._format_chain(url, chain, status, hop_limit)
                )

        await utils.answer(message, "\n\n".join(blocks))
