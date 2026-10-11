# coding: utf-8
# //@name:麻豆
# //@id:tut_madou
# //@version:1
# Source: https://fwd.uou.qzz.io/widgets/Madou_TuT.js
# Source SHA256: 13508fac439c8bb9c6d8563983527125d3ebf0d1c7e1661ddcdb838061a549f6
import base64
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from lxml import etree
from base.spider import Spider


class Spider(Spider):
    backend_parse = False
    category_mode = False

    def getName(self):
        return self.META["title"]

    @staticmethod
    def _config(value):
        if isinstance(value, str):
            value = json.loads(value) if value.strip() else {}
        if not isinstance(value, dict):
            return {}
        result = dict(value)
        for field in ("ext", "data"):
            child = result.get(field)
            if isinstance(child, str):
                child = json.loads(child) if child.strip() else {}
            if isinstance(child, dict):
                result.update(child)
        return result

    def init(self, extend=""):
        self.config = {p["name"]: p.get("value", "") for p in self.META.get("globalParams", [])}
        self.config.update(self._config(extend))
        self.base = str(self.config.get("baseUrl") or self.config.get("apiBase") or self.ORIGIN).rstrip("/")
        self.timeout = max(1, min(60, float(self.config.get("timeout", 18))))
        self.ua = self.config.get("userAgent") or self.SOURCE_UA
        old = getattr(self, "session", None)
        if old:
            old.close()
        self.session = requests.Session()
        self.cache = {}
        self.host_ready = False
        self.diagnostic = {}

    def _headers(self, referer=None):
        headers = {
            "User-Agent": self.ua, "Referer": referer or self.base + "/",
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        cookie = str(self.config.get("cookie") or "")
        if cookie and urlsplit(referer or self.base).netloc == urlsplit(self.base).netloc:
            headers["Cookie"] = "; ".join(part.strip() for part in cookie.split(";")
                                         if not part.strip().lower().startswith("cf_clearance="))
        return headers

    def _url(self, path):
        return urljoin(self.base + "/", str(path))

    def _get(self, path, referer=None, params=None, timeout=None, extra_headers=None):
        url = self._url(path)
        original = urlsplit(url)
        proxy = str(self.config.get("proxy") or "").rstrip("/")
        if proxy and original.netloc == urlsplit(self.base).netloc:
            url = proxy + original.path + ("?" + original.query if original.query else "")
        headers = self._headers(referer)
        headers.update(extra_headers or {})
        if original.netloc != urlsplit(self.base).netloc:
            headers.pop("Cookie", None)
        response = self.session.get(url, headers=headers, params=params,
                                    timeout=(min(10, timeout or self.timeout), timeout or self.timeout))
        response.raise_for_status()
        return response

    def _html(self, path, referer=None, timeout=None):
        response = self._get(path, referer, timeout=timeout)
        response.encoding = "utf-8"
        text = response.text
        if re.search(r"Just a moment|cf-browser-verification|challenge-platform|cf-chl-", text, re.I):
            raise ValueError("challenge_required")
        if not text.strip():
            raise ValueError("empty_response")
        return text

    def _json(self, path, params=None):
        data = self._get(path, params=params).json()
        if not isinstance(data, (dict, list)):
            raise ValueError("invalid_json_response")
        return data

    @staticmethod
    def _doc(text):
        return etree.HTML(text or "<html></html>")

    @staticmethod
    def _text(value):
        if hasattr(value, "itertext"):
            value = " ".join(value.itertext())
        return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()

    def _first(self, node, xpath):
        values = node.xpath(xpath) if node is not None else []
        return self._text(values[0]) if values else ""

    @staticmethod
    def _page(value):
        try:
            return max(1, int(value))
        except (ValueError, TypeError):
            return 1

    @staticmethod
    def _pack(value):
        return "tutplay:" + base64.urlsafe_b64encode(
            json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")

    @staticmethod
    def _unpack(value):
        value = str(value)
        if not value.startswith("tutplay:"):
            return {"target": value}
        raw = value[len("tutplay:"):]
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))

    @staticmethod
    def _entry(value):
        if isinstance(value, list):
            value = value[0] if value else ""
        value = str(value)
        if value.startswith("atvp_detail:"):
            value = value[len("atvp_detail:"):]
        if value.startswith("tutdetail:"):
            raw = value[len("tutdetail:"):]
            return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        return value

    @staticmethod
    def _detail_id(target):
        if str(target).startswith(("https://", "http://")):
            return "tutdetail:" + base64.urlsafe_b64encode(str(target).encode("utf-8")).decode("ascii").rstrip("=")
        return str(target)

    @staticmethod
    def _label(value):
        return str(value).replace("$", " ").replace("#", " ").replace("\r", " ").replace("\n", " ")

    def _image(self, node):
        if node is None:
            return ""
        nodes = [node] + node.xpath(".//*")
        for attribute in ("data-mediumthumb", "data-image", "data-original", "data-src",
                          "data-echo", "data-lazy-src", "data-thumb", "data-thumb_url", "src"):
            for image in nodes:
                url = image.get(attribute, "")
                if url and not re.search(r"data:|blank\.gif|loading|placeholder|thumb\.png|logo\.png", url, re.I):
                    return self._url(url)
        return ""

    def _cover(self, url):
        if not url:
            return ""
        url = self._url(url)
        if hasattr(self, "_cover_source"):
            url = self._cover_source(url)
        if not url:
            return ""
        proxy = str(self.config.get("coverProxy") or "")
        if proxy and not url.startswith(proxy):
            return proxy + quote(url, safe="") if proxy.endswith("=") else (
                proxy.rstrip("/") + "/img?" + urlencode({"url": url})
            )
        return url

    def _card(self, target, title, pic="", remark="", folder=False):
        result = {"vod_id": self._detail_id(target), "vod_name": self._text(title) or str(target),
                  "vod_pic": self._cover(pic), "vod_remarks": self._text(remark)}
        if folder:
            result.update(vod_tag="folder", style={"type": "rect", "ratio": 1.5})
        self.cache[str(target)] = result
        if len(self.cache) > 256:
            del self.cache[next(iter(self.cache))]
        return result

    @staticmethod
    def _result(rows, page, has_next=None):
        more = bool(rows) if has_next is None else has_next
        limit = len(rows) or 24
        return {"list": rows, "page": page, "pagecount": page + int(more),
                "limit": limit, "total": (page - 1) * limit + len(rows) + int(more)}

    def _error(self, error):
        code = "source_error"
        if isinstance(error, requests.exceptions.JSONDecodeError):
            code = "invalid_json_response"
        elif isinstance(error, requests.Timeout):
            code = "timeout"
        elif isinstance(error, requests.ConnectionError):
            code = "network_error"
        elif isinstance(error, requests.HTTPError):
            code = "http_%s" % error.response.status_code
        elif str(error) in ("challenge_required", "empty_response", "playback_unavailable"):
            code = str(error)
        self.diagnostic = {"error": code, "error_type": type(error).__name__,
                           "msg": "源站请求失败或响应异常"}
        return dict(self.diagnostic)

    def homeContent(self, filter):
        classes, filters = [], {}
        for index, module in enumerate(self.META["modules"]):
            if module.get("type") == "stream":
                continue
            key = module.get("id") or "module_%s" % index
            classes.append({"type_id": key, "type_name": module["title"]})
            values = []
            for parameter in module.get("params", []):
                if parameter.get("type") == "enumeration":
                    values.append({
                        "key": parameter["name"], "name": parameter["title"],
                        "value": [{"n": option["title"], "v": option["value"]}
                                  for option in parameter.get("enumOptions", [])],
                    })
            if values:
                filters[key] = values
        return {"class": classes, "filters": filters}

    def _params(self, tid, extend):
        for index, module in enumerate(self.META["modules"]):
            if str(tid) == str(module.get("id") or "module_%s" % index):
                result = dict(self.config)
                result.update({p["name"]: p.get("value", "") for p in module.get("params", [])})
                result.update(self._config(extend))
                result["_function"] = module["functionName"]
                return result
        # Folder cards carry navigation targets as category IDs.
        return dict(self._config(extend), _function="folder", _target=self._entry(tid))

    def homeVideoContent(self):
        first = self.homeContent(False)["class"][0]["type_id"]
        return {"list": self.categoryContent(first, "1", False, {})["list"]}

    def categoryContent(self, tid, pg, filter, extend):
        page = self._page(pg)
        try:
            return self._result(self._category(str(tid), page, self._params(tid, extend)), page)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            return dict(self._result([], page, False), **self._error(error))

    def searchContent(self, key, quick, pg="1"):
        page = self._page(pg)
        try:
            return self._result(self._search(str(key).strip(), page, self.config), page)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            return dict(self._result([], page, False), **self._error(error))

    def detailContent(self, ids):
        target = self._entry(ids)
        try:
            return {"list": [self._detail(target)]}
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as error:
            return dict(list=[], **self._error(error))

    def _vod(self, target, doc, groups, title="", pic="", description=""):
        cached = self.cache.get(target, {})
        title = title or self._first(doc, "//meta[@property='og:title']/@content") or self._first(doc, "//h1")
        pic = pic or self._first(doc, "//meta[@property='og:image']/@content") or cached.get("vod_pic", "")
        result = {
            "vod_id": self._detail_id(target), "vod_name": title or cached.get("vod_name") or target,
            "vod_pic": self._cover(pic),
            "vod_content": description or self._first(doc, "//meta[@name='description' or @property='og:description']/@content"),
            "vod_play_from": "$$$".join(self._label(name) for name, unused in groups),
            "vod_play_url": "$$$".join("#".join(self._label(name) + "$" + self._pack(payload)
                                                for name, payload in items) for unused, items in groups),
        }
        if not groups:
            result.update(error="playback_unavailable", msg="源站未提供可用播放数据")
        return result

    def playerContent(self, flag, id, vipFlags):
        result = {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": self._headers()}
        try:
            payload = self._unpack(self._entry(id))
            url, headers = self._resolve(payload)
            if not url or not re.match(r"^https?://", url):
                raise ValueError("playback_unavailable")
            if urlsplit(url).netloc != urlsplit(self.base).netloc:
                headers.pop("Cookie", None)
            result.update(url=url, header=headers)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as error:
            result.update(self._error(error), msg="播放地址不可用，查看 error 字段")
        return result

    @staticmethod
    def _media(text):
        text = unescape(str(text)).replace("\\/", "/").replace("\\u0026", "&").replace("\\u002F", "/")
        return list(dict.fromkeys(re.findall(r'https?://[^"\'\s<>\\]+?\.(?:m3u8|mp4)[^"\'\s<>\\]*', text, re.I)))

    @staticmethod
    def _json_after(text, pattern):
        match = re.search(pattern, text)
        if not match:
            return {}
        raw = text[match.end():].lstrip()
        try:
            return json.JSONDecoder().raw_decode(raw)[0]
        except ValueError:
            return {}

    def _variants(self, url, referer=None):
        text = self._html(url, referer)
        if not text.lstrip().startswith("#EXTM3U"):
            raise ValueError("invalid_hls")
        result = []
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue
            height = re.search(r"RESOLUTION=\d+x(\d+)", line)
            bandwidth = re.search(r"BANDWIDTH=(\d+)", line)
            for path in lines[index + 1:]:
                if path.strip() and not path.startswith("#"):
                    score = int(height[1]) if height else int(bandwidth[1]) if bandwidth else 0
                    result.append((score, urljoin(url, path.strip())))
                    break
        return sorted(set(result), reverse=True)

    def localProxy(self, param):
        return [404, "text/plain; charset=utf-8", "No local proxy route"]

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(m3u8|mp4|mkv|webm|flv)(?:[?#]|$)", str(url), re.I))

    def manualVideoCheck(self):
        return False

    def getDependence(self):
        return []

    def destroy(self):
        if getattr(self, "session", None):
            self.session.close()

    META = {'id': 'forward.madou.tut', 'title': '麻豆', 'version': '1.0.0', 'requiredVersion': '0.0.1', 'description': '麻豆社在线：分栏分类/搜索，详情解析 HLS | 欢迎加入 宝宝巴士乐园 t.me/nostremby', 'author': '宝宝巴士', 'site': 'https://t.me/nostremby', 'detailCacheDuration': 300, 'globalParams': [{'name': 'baseUrl', 'title': '站点域名', 'type': 'input', 'value': 'https://madou.club'}], 'modules': [{'id': 'home', 'title': '最新', 'functionName': 'loadHome', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'madou', 'title': '麻豆传媒', 'functionName': 'loadMadou', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'hkd', 'title': 'HongKongDoll', 'functionName': 'loadHkd', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'guodong', 'title': '果冻传媒', 'functionName': 'loadGuodong', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'mitao', 'title': '蜜桃影像', 'functionName': 'loadMitao', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'tianmei', 'title': '天美传媒', 'functionName': 'loadTianmei', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'jingdong', 'title': '精东影业', 'functionName': 'loadJingdong', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'jy91', 'title': '91制片厂', 'functionName': 'load91', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'huangjia', 'title': '皇家华人', 'functionName': 'loadHuangjia', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'tuzi', 'title': '兔子先生', 'functionName': 'loadTuzi', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'xingkong', 'title': '星空无限', 'functionName': 'loadXingkong', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'aidou', 'title': '爱豆', 'functionName': 'loadAidou', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'daoyan', 'title': '导演系列', 'functionName': 'loadDaoyan', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'daxiang', 'title': '大象传媒', 'functionName': 'loadDaxiang', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'maozhua', 'title': '猫爪影像', 'functionName': 'loadMaozhua', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'xingba', 'title': '杏吧', 'functionName': 'loadXingba', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'lebo', 'title': '乐播传媒', 'functionName': 'loadLebo', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'psycho', 'title': 'PsychoPorn', 'functionName': 'loadPsycho', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'fanwai', 'title': '番外篇', 'functionName': 'loadFanwai', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'huaxu', 'title': '花絮', 'functionName': 'loadHuaxu', 'cacheDuration': 300, 'params': [{'name': 'page', 'title': '页码', 'type': 'page'}]}, {'id': 'loadResource', 'title': '播放资源', 'functionName': 'loadResource', 'type': 'stream', 'cacheDuration': 0, 'params': []}], 'search': {'title': '搜索', 'functionName': 'search', 'params': [{'name': 'keyword', 'title': '关键词', 'type': 'input'}, {'name': 'page', 'title': '页码', 'type': 'page'}]}}
    ORIGIN = 'https://madou.club'
    SOURCE_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ROUTES = {'loadHome': 'home', 'loadMadou': '麻豆传媒', 'loadHkd': 'hongkongdoll', 'loadGuodong': '果冻传媒', 'loadMitao': '蜜桃影像', 'loadTianmei': '天美传媒', 'loadJingdong': '精东影业', 'load91': '91制片厂', 'loadHuangjia': '皇家华人', 'loadTuzi': '兔子先生', 'loadXingkong': '星空无限传媒', 'loadAidou': '爱豆', 'loadDaoyan': '麻豆导演系列', 'loadDaxiang': '大象传媒', 'loadMaozhua': '猫爪影像', 'loadXingba': '杏吧', 'loadLebo': '乐播传媒', 'loadPsycho': 'psychoporntw', 'loadFanwai': '麻豆番外篇', 'loadHuaxu': '麻豆花絮'}

    def _parse_list(self, text):
        rows = []
        for node in self._doc(text).xpath("//article[contains(@class,'excerpt')]"):
            path = self._first(node, ".//a[contains(@class,'thumbnail')]/@href|.//h2/a/@href")
            if not path:
                continue
            pic = re.sub(r"-\d+x\d+(?=\.(?:jpg|jpeg|png|webp))", "", self._image(node))
            title = self._first(node, ".//h2/a") or self._first(node, ".//a/@title")
            rows.append(self._card(self._url(path), title, pic))
        return rows

    def _category(self, tid, page, params):
        slug = self.ROUTES[params["_function"]]
        path = "/" if slug == "home" else "/category/" + quote(slug, safe="")
        if page > 1:
            path = path.rstrip("/") + "/page/%s" % page
        return self._parse_list(self._html(path))

    def _search(self, keyword, page, params):
        path = "/" if page == 1 else "/page/%s/" % page
        return self._parse_list(self._html(path + "?" + urlencode({"s": keyword}))) if keyword else []

    def _share(self, text):
        match = re.search(r'https?://[^"\'\s<>]+/share/[A-Za-z0-9]+', text)
        return match[0] if match else ""

    def _detail(self, target):
        page = self._url(target)
        text = self._html(page)
        doc = self._doc(text)
        share = self._share(text)
        groups = [("麻豆", [("最高画质", {"share": share, "best": True}),
                          ("原画 Master", {"share": share, "best": False})])] if share else []
        return self._vod(target, doc, groups, pic=self._first(doc, "//img[contains(@src,'/covers/')]/@src"))

    def _resolve(self, payload):
        share = payload.get("share")
        if not share:
            text = self._html(payload["target"])
            share = self._share(text)
        text = self._html(share)
        match = re.search(r'var\s+m3u8\s*=\s*["\']([^"\']+)', text)
        if not match:
            raise ValueError("playback_unavailable")
        url = urljoin(share, match[1])
        token = re.search(r'var\s+token\s*=\s*["\']([^"\']*)', text)
        if token and token[1]:
            url += ("&" if "?" in url else "?") + urlencode({"token": token[1]})
        if payload.get("best", True):
            try:
                variants = self._variants(url, self.base + "/")
                if variants:
                    url = variants[0][1]
            except (requests.RequestException, ValueError):
                # The master remains a valid player handoff if this probe times out.
                pass
        headers = self._headers(self.base + "/")
        parsed = urlsplit(share)
        headers["Origin"] = parsed.scheme + "://" + parsed.netloc
        return url, headers
