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
