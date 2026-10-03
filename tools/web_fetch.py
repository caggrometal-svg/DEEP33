from __future__ import annotations

import copy
import hashlib
import ipaddress
import os
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

MAX_REDIRECTS=3
MAX_RESPONSE_BYTES=512*1024

def _max_response_bytes() -> int:
    try:
        return max(64*1024, min(MAX_RESPONSE_BYTES, int(os.getenv("WEB_FETCH_MAX_RESPONSE_BYTES", str(MAX_RESPONSE_BYTES)))))
    except ValueError:
        return MAX_RESPONSE_BYTES
MAX_TEXT_CHARS=50_000
DEFAULT_TIMEOUT_SECONDS=8.0
DEFAULT_USER_AGENT="DEEP33-WebFetcher/1.0"
FETCH_CACHE_TTL_SECONDS = max(
    30.0, min(600.0, float(os.getenv("WEB_FETCH_CACHE_TTL_SECONDS", "300")))
)
_FETCH_CACHE: dict[str, tuple[float, dict]] = {}


BLOCKED_HOSTNAMES={"localhost","localhost.localdomain","ip6-localhost","ip6-loopback","metadata","metadata.google.internal","instance-data","instance-data.ec2.internal","169.254.169.254","100.100.100.200"}
REDIRECT_STATUSES={301,302,303,307,308}
ALLOWED_CONTENT_TYPES={"text/html","application/xhtml+xml","text/plain"}
BLOCKED_HTML_TAGS={"script","style","noscript","svg","canvas","template"}

class WebFetchError(RuntimeError): pass
class SSRFBlockedError(WebFetchError): pass
class DownloadLimitError(WebFetchError): pass

class HTMLTextExtractor(HTMLParser):
    def __init__(self)->None:
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.title_parts=[]; self._skip_depth=0; self._in_title=False
    def handle_starttag(self,tag,attrs):
        tag=tag.lower()
        if tag=="title": self._in_title=True
        if tag in BLOCKED_HTML_TAGS:
            self._skip_depth+=1; return
        if tag in {"p","div","section","article","main","aside","header","footer","nav","li","ul","ol","h1","h2","h3","h4","h5","h6","br","tr","td","th","pre","blockquote"}:
            self.parts.append("\n")
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag=="title": self._in_title=False
        if tag in BLOCKED_HTML_TAGS:
            self._skip_depth=max(0,self._skip_depth-1); return
        if tag in {"p","div","section","article","main","aside","header","footer","nav","li","ul","ol","h1","h2","h3","h4","h5","h6","br","tr","td","th","pre","blockquote"}:
            self.parts.append("\n")
    def handle_data(self,data):
        if self._skip_depth: return
        value=" ".join(data.split())
        if not value: return
        if self._in_title: self.title_parts.append(value)
        self.parts.append(value)
    @property
    def title(self): return " ".join(self.title_parts).strip()
    @property
    def text(self):
        lines=[]; blank=False
        for raw in "\n".join(self.parts).splitlines():
            line=" ".join(raw.split()).strip()
            if not line:
                if lines and not blank: lines.append("")
                blank=True; continue
            lines.append(line); blank=False
        return "\n".join(lines).strip()

def _is_public_ip(value):
    try: return ipaddress.ip_address(value).is_global
    except ValueError: return False

def _validate_hostname(hostname):
    host=hostname.rstrip(".").lower()
    if not host or host in BLOCKED_HOSTNAMES: raise SSRFBlockedError("WEB_FETCH_SSRF_BLOCKED")
    try: ip=ipaddress.ip_address(host)
    except ValueError: return
    if not ip.is_global: raise SSRFBlockedError("WEB_FETCH_PRIVATE_IP_BLOCKED")

def _resolve_public_addresses(hostname):
    _validate_hostname(hostname)
    try:
        literal=ipaddress.ip_address(hostname); return [str(literal)]
    except ValueError: pass
    try: infos=socket.getaddrinfo(hostname,443,type=socket.SOCK_STREAM)
    except socket.gaierror as exc: raise WebFetchError("WEB_FETCH_DNS_FAILED") from exc
    addresses=sorted({info[4][0] for info in infos})
    if not addresses: raise WebFetchError("WEB_FETCH_DNS_EMPTY")
    if not all(_is_public_ip(a) for a in addresses): raise SSRFBlockedError("WEB_FETCH_DNS_PRIVATE_IP_BLOCKED")
    return addresses

def validate_public_url(url):
    candidate=url.strip(); parsed=urlparse(candidate)
    if parsed.scheme.lower() not in {"http","https"}: raise SSRFBlockedError("WEB_FETCH_SCHEME_BLOCKED")
    if parsed.username or parsed.password: raise SSRFBlockedError("WEB_FETCH_CREDENTIALS_BLOCKED")
    if not parsed.hostname: raise WebFetchError("WEB_FETCH_HOST_REQUIRED")
    if parsed.port not in (None,80,443): raise SSRFBlockedError("WEB_FETCH_PORT_BLOCKED")
    _resolve_public_addresses(parsed.hostname)
    return candidate

async def _read_limited(response,limit):
    total=0; chunks=[]
    async for chunk in response.aiter_bytes():
        total+=len(chunk)
        if total>limit: raise DownloadLimitError("WEB_FETCH_DOWNLOAD_TOO_LARGE")
        chunks.append(chunk)
    return b"".join(chunks)

def _extract_text(content,content_type,max_text_chars):
    charset="utf-8"; lower=content_type.lower()
    if "charset=" in lower: charset=lower.split("charset=",1)[1].split(";",1)[0].strip() or charset
    decoded=content.decode(charset,errors="replace"); mime=content_type.split(";",1)[0].strip().lower()
    if mime=="text/plain":
        return "", "\n".join(line.strip() for line in decoded.splitlines() if line.strip())[:max_text_chars]
    parser=HTMLTextExtractor(); parser.feed(decoded); parser.close()
    return parser.title[:500],parser.text[:max_text_chars]

async def fetch_page(url,*,timeout_seconds=DEFAULT_TIMEOUT_SECONDS,max_redirects=MAX_REDIRECTS,max_text_chars=MAX_TEXT_CHARS,user_agent=DEFAULT_USER_AGENT):
    if not 1<=max_redirects<=MAX_REDIRECTS: raise ValueError("max_redirects must be between 1 and 3")
    timeout_seconds=max(1.0,min(8.0,timeout_seconds)); max_text_chars=max(1000,min(MAX_TEXT_CHARS,max_text_chars))
    original_url=url.strip(); current_url=validate_public_url(original_url); redirects=[]
    cache_key=hashlib.sha256(
        f"{current_url}\x1f{max_redirects}\x1f{max_text_chars}\x1f{user_agent}".encode("utf-8")
    ).hexdigest()
    cached=_FETCH_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return copy.deepcopy(cached[1])
    timeout=httpx.Timeout(connect=min(timeout_seconds,5.0),read=timeout_seconds,write=timeout_seconds,pool=timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,headers={"User-Agent":user_agent,"Accept":"text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1"}) as client:
        for hop in range(max_redirects+1):
            current_url=validate_public_url(current_url)
            try:
                async with client.stream("GET",current_url) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location=response.headers.get("location")
                        if not location: raise WebFetchError("WEB_FETCH_REDIRECT_LOCATION_MISSING")
                        if hop>=max_redirects: raise WebFetchError("WEB_FETCH_REDIRECT_LIMIT")
                        next_url=urljoin(current_url,location); validate_public_url(next_url)
                        redirects.append(next_url); current_url=next_url; continue
                    if response.status_code>=400: raise WebFetchError(f"WEB_FETCH_HTTP_{response.status_code}")
                    content_type=response.headers.get("content-type",""); mime=content_type.split(";",1)[0].strip().lower()
                    if mime not in ALLOWED_CONTENT_TYPES: raise WebFetchError("WEB_FETCH_CONTENT_TYPE_BLOCKED")
                    declared=response.headers.get("content-length")
                    if declared:
                        try:
                            if int(declared)>_max_response_bytes(): raise DownloadLimitError("WEB_FETCH_DOWNLOAD_TOO_LARGE")
                        except ValueError: pass
                    content=await _read_limited(response,_max_response_bytes())
                title,text=_extract_text(content,content_type,max_text_chars); text=text.strip()
                if not text: raise WebFetchError("WEB_FETCH_EMPTY_TEXT")
                result={"ok":True,"url":original_url,"final_url":current_url,"title":title or current_url,"text":text,"content_type":mime,"bytes":len(content),"redirects":len(redirects)}
                _FETCH_CACHE[cache_key]=(time.monotonic()+FETCH_CACHE_TTL_SECONDS,copy.deepcopy(result))
                return result
            except httpx.TimeoutException as exc: raise WebFetchError("WEB_FETCH_TIMEOUT") from exc
            except httpx.HTTPError as exc: raise WebFetchError("WEB_FETCH_NETWORK_ERROR") from exc
    raise WebFetchError("WEB_FETCH_REDIRECT_LIMIT")
