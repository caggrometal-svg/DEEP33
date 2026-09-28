from __future__ import annotations

import html, os
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse
import httpx
from tools.web_fetch import validate_public_url

TAVILY_URL="https://api.tavily.com/search"
DUCKDUCKGO_URL="https://html.duckduckgo.com/html/"
DEFAULT_TIMEOUT_SECONDS=8.0
DEFAULT_MAX_RESULTS=5
MAX_QUERY_CHARS=1000

class WebSearchError(RuntimeError): pass

class DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.results=[]; self._current=None; self._capture=None; self._buffer=[]
    def _flush(self):
        if self._current and self._current.get("title") and self._current.get("url"): self.results.append(dict(self._current))
        self._current=None; self._capture=None; self._buffer=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs); classes=set((a.get("class") or "").split())
        if tag=="a" and "result__a" in classes:
            self._flush(); self._current={"url":_unwrap_ddg_url(a.get("href") or ""),"title":"","snippet":""}; self._capture="title"; self._buffer=[]; return
        if self._current and "result__snippet" in classes: self._capture="snippet"; self._buffer=[]
    def handle_endtag(self,tag):
        if not self._capture: return
        if self._capture=="title" and tag=="a":
            self._current["title"]=" ".join(self._buffer).strip(); self._capture=None; self._buffer=[]
        elif self._capture=="snippet" and tag in {"div","td"}:
            self._current["snippet"]=" ".join(self._buffer).strip(); self._capture=None; self._buffer=[]
    def handle_data(self,data):
        if self._capture:
            value=html.unescape(data).strip()
            if value: self._buffer.append(value)
    def close(self):
        super().close(); self._flush()

def _unwrap_ddg_url(value):
    if not value: return ""
    absolute=value if value.startswith(("http://","https://")) else f"https:{value}" if value.startswith("//") else value
    target=parse_qs(urlparse(absolute).query).get("uddg",[None])[0]
    return unquote(target) if target else absolute

def _normalise_results(results,limit):
    out=[]; seen=set()
    for item in results:
        title=str(item.get("title") or "").strip(); url=str(item.get("url") or "").strip(); snippet=str(item.get("content") or item.get("snippet") or "").strip()
        if not title or not url: continue
        try: url=validate_public_url(url)
        except Exception: continue
        if url in seen: continue
        seen.add(url); out.append({"title":title[:300],"url":url[:2000],"snippet":snippet[:1500]})
        if len(out)>=limit: break
    return out

async def _tavily_search(query,api_key,timeout_seconds,max_results):
    payload={"query":query,"search_depth":"basic","topic":"general","max_results":max_results,"include_answer":False,"include_raw_content":False,"include_images":False,"safe_search":False}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds),follow_redirects=True,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json","User-Agent":"DEEP33-WebSearch/1.0"}) as client:
        response=await client.post(TAVILY_URL,json=payload)
        if response.status_code>=400: raise WebSearchError(f"WEB_SEARCH_TAVILY_HTTP_{response.status_code}")
        data=response.json()
    raw=data.get("results")
    if not isinstance(raw,list): raise WebSearchError("WEB_SEARCH_TAVILY_INVALID_RESPONSE")
    return _normalise_results(raw,max_results)

async def _duckduckgo_search(query,timeout_seconds,max_results):
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds),follow_redirects=True,headers={"User-Agent":"DEEP33-WebSearch/1.0","Accept":"text/html,application/xhtml+xml"}) as client:
        response=await client.get(DUCKDUCKGO_URL,params={"q":query,"kl":"wt-wt"})
        if response.status_code>=400: raise WebSearchError(f"WEB_SEARCH_DDG_HTTP_{response.status_code}")
        if len(response.content)>2*1024*1024: raise WebSearchError("WEB_SEARCH_DDG_RESPONSE_TOO_LARGE")
    parser=DuckDuckGoParser(); parser.feed(response.text); parser.close()
    return _normalise_results(parser.results,max_results)

async def search_web(query,*,timeout_seconds=DEFAULT_TIMEOUT_SECONDS,max_results=DEFAULT_MAX_RESULTS,provider=None,api_key=None):
    cleaned=" ".join(query.split()).strip()
    if not cleaned: raise WebSearchError("WEB_SEARCH_QUERY_REQUIRED")
    if len(cleaned)>MAX_QUERY_CHARS: raise WebSearchError("WEB_SEARCH_QUERY_TOO_LONG")
    timeout_seconds=max(1.0,min(8.0,timeout_seconds)); max_results=max(1,min(8,int(max_results)))
    selected=(provider or os.getenv("WEB_SEARCH_PROVIDER","auto")).strip().lower()
    key=(api_key if api_key is not None else os.getenv("WEB_SEARCH_API_KEY","")).strip()
    if selected=="tavily":
        attempts=["tavily","duckduckgo"] if os.getenv("WEB_SEARCH_FALLBACK_DDG","true").lower()=="true" else ["tavily"]
    elif selected=="duckduckgo": attempts=["duckduckgo"]
    else: attempts=["tavily","duckduckgo"] if key else ["duckduckgo"]
    errors=[]
    for name in attempts:
        try:
            if name=="tavily":
                if not key: errors.append("tavily:missing_api_key"); continue
                results=await _tavily_search(cleaned,key,timeout_seconds,max_results)
            else: results=await _duckduckgo_search(cleaned,timeout_seconds,max_results)
            return {"ok":True,"query":cleaned,"provider":name,"results":results,"sources":results}
        except Exception as exc: errors.append(f"{name}:{type(exc).__name__}")
    raise WebSearchError("WEB_SEARCH_FAILED:" + ",".join(errors))

def web_search_status():
    provider=os.getenv("WEB_SEARCH_PROVIDER","auto").strip().lower(); key=bool(os.getenv("WEB_SEARCH_API_KEY","").strip())
    active="tavily" if provider=="tavily" and key else "duckduckgo" if provider=="duckduckgo" or not key else "tavily"
    return {"enabled":True,"configured_provider":provider,"active_provider":active,"tavily_configured":key,"fallback_duckduckgo":os.getenv("WEB_SEARCH_FALLBACK_DDG","true").lower()=="true","max_results":max(1,min(8,int(os.getenv("WEB_SEARCH_MAX_RESULTS","5")))),"timeout_seconds":max(1.0,min(8.0,float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS","8"))))}
