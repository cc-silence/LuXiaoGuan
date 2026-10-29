import test from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import gateway from "./src/index.js";
const file="/files/"+"a".repeat(64)+"/RouterManager_v2.0.8_alpha_20261019_1200.zip";
function object(body) {return {body,size:Buffer.byteLength(body),httpEtag:'"etag"',text:async()=>body,writeHttpMetadata(){}};}
function env(version="2.0.14_alpha",stable=null) {
  const calls=[];const manifest=object(JSON.stringify({payload:{version}}));
  const store={calls,TEST_CODE_SHA256:createHash("sha256").update("fixture-code").digest("hex"),UPDATES:{
    async get(key,options) {calls.push([key,options]);return key==="channel/manifest.json" ? manifest : key==="channel/stable.json" ? stable && object(JSON.stringify({payload:{version:stable}})) : object("payload");},
    async head(key) {calls.push([key]);return object("payload");}
  }};return store;
}
const get=(path,store,options={})=>gateway.fetch(new Request("https://example.com"+path,options),store);
test("no code cannot receive alpha or beta, including HEAD and legacy manifest",async()=>{
 for(const version of ["2.0.14_alpha","2.0.14_beta","2.0.14_rc"]) {
  const store=env(version);
  for(const method of ["GET","HEAD"]) {const r=await get("/manifest",store,{method});assert.equal(r.status,204);assert.equal(await r.text(),"");assert.equal(r.headers.get("Cache-Control"),"no-store");}
 }
});
test("valid code receives preview, wrong code or absent secret fails closed",async()=>{
 const store=env();
 let r=await get("/manifest",store,{headers:{"X-RouterManager-Test-Code":"fixture-code"}});
 assert.equal(r.status,200);assert.equal(JSON.parse(await r.text()).payload.version,"2.0.14_alpha");assert.equal(r.headers.get("Cache-Control"),"no-store");assert.equal(r.headers.get("Vary"),"X-RouterManager-Test-Code");
 for(const code of ["wrong-code", "x".repeat(97)])assert.equal((await get("/manifest",store,{headers:{"X-RouterManager-Test-Code":code}})).status,403);
 delete store.TEST_CODE_SHA256;assert.equal((await get("/manifest",store,{headers:{"X-RouterManager-Test-Code":"fixture-code"}})).status,403);
});
test("ordinary users receive last formal version while testers receive newer preview",async()=>{
 const store=env("2.0.14_beta","2.0.13");
 const a=await get("/manifest",store);assert.equal(JSON.parse(await a.text()).payload.version,"2.0.13");
 const b=await get("/manifest",store,{headers:{"X-RouterManager-Test-Code":"fixture-code"}});assert.equal(JSON.parse(await b.text()).payload.version,"2.0.14_beta");
 const legacy=await get("/manifest",env("2.0.13"));assert.equal(JSON.parse(await legacy.text()).payload.version,"2.0.13");
});
test("bad or oversized manifests are rejected",async()=>{
 const store=env();store.UPDATES.get=async()=>object("bad json");assert.equal((await get("/manifest",store)).status,503);
 store.UPDATES.get=async()=>object("x".repeat(65537));assert.equal((await get("/manifest",store)).status,503);
});
test("files support streaming, HEAD, ranges and immutable caching",async()=>{
 const store=env();const r=await get(file,store);assert.equal(r.status,200);assert.equal(await r.text(),"payload");assert.match(r.headers.get("Content-Disposition"),/attachment/);
 const head=await get(file,store,{method:"HEAD"});assert.equal(await head.text(),"");
 store.UPDATES.get=async()=>({...object("ay"),size:7,range:{offset:1,length:2}});
 const partial=await get(file,store,{headers:{Range:"bytes=1-2"}});assert.equal(partial.status,206);assert.equal(partial.headers.get("Content-Range"),"bytes 1-2/7");
 const full=await get(file,store);assert.equal(full.status,200);assert.equal(full.headers.get("Content-Range"),null);
});
test("invalid routes and write methods cannot access R2",async()=>{
 const store=env();for(const path of ["/","/channel/manifest.json","/manifest/","/files/private.pem"])assert.equal((await get(path,store)).status,404);
 assert.equal((await get("/manifest",store,{method:"PUT",body:"x"})).status,405);assert.equal(store.calls.length,0);
});
test("content-addressed objects remain read-only and immutable",async()=>{
 for(const [kind,suffix] of [["catalog",".json"],["blobs",".z"]]) {const store=env();const r=await get("/"+kind+"/"+"a".repeat(64)+suffix,store);assert.equal(r.status,200);assert.match(r.headers.get("Cache-Control"),/immutable/);}
});

test("offline common and capture manifests use independent read-only channels",async()=>{
 for(const kind of ["common","capture"]) {
  const store=env();
  const r=await get("/offline/openwrt-"+kind+"/manifest",store);
  assert.equal(r.status,200);assert.equal(await r.text(),"payload");
  assert.equal(r.headers.get("Cache-Control"),"no-store");
  assert.equal(store.calls[0][0],"channel/openwrt-"+kind+".json");
  const head=await get("/offline/openwrt-"+kind+"/manifest",store,{method:"HEAD"});
  assert.equal(await head.text(),"");
  store.UPDATES.get=async()=>null;
  assert.equal((await get("/offline/openwrt-"+kind+"/manifest",store)).status,404);
  store.UPDATES.get=async()=>object("x".repeat(65537));
  assert.equal((await get("/offline/openwrt-"+kind+"/manifest",store)).status,503);
 }
});
test("two offline ZIP routes support HEAD, ranges and immutable caching",async()=>{
 for(const kind of ["common","capture"]) {
  const title=kind[0].toUpperCase()+kind.slice(1);
  const path="/offline/openwrt-"+kind+"/files/"+"a".repeat(64)+"/RouterManager_OpenWrt_"+title+"_Offline_fixture.zip";
  const store=env();const r=await get(path,store);
  assert.equal(r.status,200);assert.equal(r.headers.get("Content-Type"),"application/zip");
  assert.equal(await r.text(),"payload");assert.match(r.headers.get("Cache-Control"),/immutable/);
  assert.equal(await (await get(path,store,{method:"HEAD"})).text(),"");
  store.UPDATES.get=async()=>({...object("ay"),size:7,range:{offset:1,length:2}});
  const range=await get(path,store,{headers:{Range:"bytes=1-2"}});
  assert.equal(range.status,206);assert.equal(range.headers.get("Content-Range"),"bytes 1-2/7");
 }
});
test("offline cross-kind filenames, unknown channels and write access are rejected",async()=>{
 const store=env();const prefix="/offline/openwrt-common/files/"+"a".repeat(64)+"/";
 for(const path of [prefix+"RouterManager_OpenWrt_Capture_Offline_x.zip","/offline/openwrt-other/manifest","/offline/openwrt-common/private.pem"])
  assert.equal((await get(path,store)).status,404);
 for(const method of ["PUT","POST","DELETE"])assert.equal((await get("/offline/openwrt-common/manifest",store,{method})).status,405);
 assert.equal(store.calls.length,0);
});


test("renamed LuXiaoGuan packages retain read-only download support",async()=>{
 const path=file.replace("RouterManager_v","LuXiaoGuan_v");
 const store=env();const r=await get(path,store);
 assert.equal(r.status,200);assert.equal(await r.text(),"payload");
 assert.equal(store.calls[0][0],path.slice(1));
 assert.equal((await get(path,store,{method:"HEAD"})).status,200);
 assert.equal((await get(path,store,{method:"PUT",body:"x"})).status,405);
});


test("Linux manifests use independent slots and reject a Windows payload",async()=>{
 const store=env();
 const reads=[];
 store.UPDATES.get=async key=>{
  reads.push(key);
  if(key==="channel/linux-x64/stable.json")return object(JSON.stringify({payload:{platform:"linux-x64",version:"1.0.1"}}));
  if(key==="channel/linux-x64/alpha.json")return object(JSON.stringify({payload:{platform:"linux-x64",version:"1.0.2_alpha"}}));
  return null;
 };
 let response=await get("/manifest?platform=linux-x64",store);
 assert.equal(response.status,200);
 assert.equal(JSON.parse(await response.text()).payload.platform,"linux-x64");
 response=await get("/manifest?platform=linux-x64",store,{headers:{"X-RouterManager-Test-Code":"fixture-code"}});
 assert.equal(JSON.parse(await response.text()).payload.version,"1.0.2_alpha");
 assert.deepEqual(reads,["channel/linux-x64/stable.json","channel/linux-x64/alpha.json"]);
 store.UPDATES.get=async()=>object(JSON.stringify({payload:{platform:"win-x64",version:"1.0.1"}}));
 assert.equal((await get("/manifest?platform=linux-x64",store)).status,503);
 for(const query of ["platform=foreign","platform=win-x64&platform=linux-x64","other=linux"])
  assert.equal((await get("/manifest?"+query,store)).status,400);
});

test("alpha maintenance has retry feedback and does not affect stable or another OS",async()=>{
 const store=env();
 store.UPDATES.get=async key=>{
  if(key==="channel/manifest.json")return object(JSON.stringify({schema:1,product:"RouterManager",state:"publishing",platform:"win-x64",channel:"alpha",version:"1.0.19_alpha",sha256:"a".repeat(64)}));
  if(key==="channel/stable.json")return object(JSON.stringify({payload:{platform:"win-x64",version:"1.0.1"}}));
  if(key==="channel/linux-x64/stable.json")return object(JSON.stringify({payload:{platform:"linux-x64",version:"1.0.2"}}));
  return null;
 };
 const response=await get("/manifest",store,{headers:{"X-RouterManager-Test-Code":"fixture-code"}});
 assert.equal(response.status,503);assert.equal(response.headers.get("Retry-After"),"30");
 assert.equal(response.headers.get("Cache-Control"),"no-store");
 assert.equal((await get("/manifest",store)).status,200);
 assert.equal((await get("/manifest?platform=linux-x64",store)).status,200);
});

test("Linux archives support the existing streaming and range contract",async()=>{
 const store=env();const path=file.replace("/files/","/files/linux-x64/");
 assert.equal((await get(path,store)).status,200);
 assert.equal((await get(path,store,{method:"HEAD"})).status,200);
 assert.equal((await get(path.replace("linux-x64","linux-other"),store)).status,404);
 assert.equal((await get(path,store,{method:"DELETE"})).status,405);
});


test("update blobs resume with bounded ranges, suffixes and unsatisfied ranges",async()=>{
 const path="/blobs/"+"b".repeat(64)+".z";
 const store=env();
 store.UPDATES.get=async(key, options={})=>{
   store.calls.push([key,options]);
   if(options.range) {
     const {offset,length}=options.range;
     return {...object("payload".slice(offset,offset+length)),size:7,range:{offset,length}};
   }
   return object("payload");
 };
 for(const [range,body,contentRange] of [["bytes=3-","load","bytes 3-6/7"],["bytes=-2","ad","bytes 5-6/7"],["bytes=1-999","ayload","bytes 1-6/7"]]) {
   const response=await get(path,store,{headers:{Range:range}});
   assert.equal(response.status,206);assert.equal(await response.text(),body);
   assert.equal(response.headers.get("Content-Range"),contentRange);
   assert.equal(response.headers.get("Content-Length"),String(body.length));
   assert.equal(response.headers.get("Accept-Ranges"),"bytes");
   assert.equal(response.headers.get("Content-Type"),"application/octet-stream");
 }
 for(const range of ["bytes=7-","bytes=999-","bytes=4-2","bytes=-0"]) {
   const response=await get(path,store,{headers:{Range:range}});
   assert.equal(response.status,416);assert.equal(response.headers.get("Content-Range"),"bytes */7");
 }
 for(const headers of [{Range:"bytes=3-","If-Range":'"old-etag"'},{Range:"invalid"},{Range:"bytes=0-1,4-5"}]) {
   const response=await get(path,store,{headers});assert.equal(response.status,200);assert.equal(await response.text(),"payload");
 }
 const head=await get(path,store,{method:"HEAD",headers:{Range:"bytes=3-"}});
 assert.equal(head.status,200);assert.equal(head.headers.get("Content-Length"),"7");assert.equal(await head.text(),"");
});
