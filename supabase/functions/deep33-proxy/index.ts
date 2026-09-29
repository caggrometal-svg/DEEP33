const UPSTREAM="https://deep33.c-33.blitz.cloud";
const cors={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Headers":"authorization, x-client-info, apikey, content-type, x-deep33-session-id, x-request-id, x-idempotency-key","Access-Control-Allow-Methods":"GET,POST,PUT,OPTIONS"};
const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{...cors,"Content-Type":"application/json; charset=utf-8","Cache-Control":"no-store"}});
function normalizePath(pathname:string){return pathname.replace(/^\/functions\/v1\/deep33-proxy/,"").replace(/^\/deep33-proxy/,"")||"/";}
async function fetchUpstream(path:string,init:RequestInit={},sessionId="deep33-edge"){const headers=new Headers(init.headers||{});headers.delete("Authorization");headers.delete("apikey");headers.delete("x-client-info");if(!headers.has("x-deep33-session-id"))headers.set("x-deep33-session-id",sessionId);return fetch(UPSTREAM+path,{...init,headers,redirect:"follow"});}
async function edgeSearch(query:string){const q=query.trim();if(!q)return {ok:false,error:"SEARCH_QUERY_REQUIRED",results:[]};const sid="edge-search-"+crypto.randomUUID();const res=await fetchUpstream("/v1/web/search?q="+encodeURIComponent(q),{},sid);const data=await res.json().catch(()=>({}));if(!res.ok)return {ok:false,error:"SEARCH_HTTP_"+res.status,results:[],upstream:data};return data;}
Deno.serve(async(req)=>{
if(req.method==="OPTIONS")return new Response("ok",{headers:cors});
const url=new URL(req.url);const path=normalizePath(url.pathname);
if(path==="/v1/connectivity/audit"&&req.method==="GET"){
 const started=performance.now();let upstream={health:false,ready:false,web_status:0};
 try{const [h,r,s]=await Promise.all([fetchUpstream("/health"),fetchUpstream("/ready"),fetchUpstream("/v1/web/status")]);upstream.health=h.ok;upstream.ready=r.ok;upstream.web_status=s.status;}catch(e){return json({status:"FAIL",edge:"PASS",internet:"FAIL",error:e instanceof Error?e.message:String(e),timestamp:new Date().toISOString()},502);}
 let search;try{search=await edgeSearch("DEEP33 internet");}catch(e){search={ok:false,error:e instanceof Error?e.message:String(e),results:[]};}
 const upstreamReady=upstream.ready===true;
 return json({status:upstream.health===true&&upstreamReady&&search.ok===true?"PASS":"FAIL",edge:"PASS",internet:search.ok===true?"PASS":"FAIL",ready:upstreamReady?"PASS":"FAIL",search,upstream,latency_ms:Math.round(performance.now()-started),timestamp:new Date().toISOString()});
}
if(path==="/v1/search"&&req.method==="GET"){try{return json(await edgeSearch(url.searchParams.get("q")||url.searchParams.get("query")||""));}catch(e){return json({ok:false,error:e instanceof Error?e.message:String(e),results:[]},502);}}
if(path.startsWith("/v1/")||path==="/health"||path==="/ready"||path==="/metrics"){
 try{const body=req.method==="GET"||req.method==="HEAD"?undefined:await req.arrayBuffer();const headers=new Headers();for(const name of ["content-type","accept","x-deep33-session-id","x-request-id","x-idempotency-key"]){const v=req.headers.get(name);if(v)headers.set(name,v);}const res=await fetchUpstream(path+url.search,{method:req.method,headers,body},req.headers.get("x-deep33-session-id")||"deep33-mobile");const out=new Headers(cors);for(const name of ["content-type","cache-control","x-request-id"]){const v=res.headers.get(name);if(v)out.set(name,v);}return new Response(res.body,{status:res.status,headers:out});}
 catch(e){return json({detail:"DEEP33_UPSTREAM_UNAVAILABLE",error:e instanceof Error?e.message:String(e)},502);}
}
return json({status:"PASS",service:"DEEP33 Internet Edge",path,timestamp:new Date().toISOString()});
});