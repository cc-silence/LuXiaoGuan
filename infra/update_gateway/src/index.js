// Public read-only gateway. Uploads are possible only through the authenticated publisher.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!["GET", "HEAD"].includes(request.method))
      return new Response("Method not allowed", {status: 405, headers: {Allow: "GET, HEAD"}});
    if (url.pathname === "/health") {
      return Response.json({product: "RouterManager", service: "releases", schema: 1},
        {headers: {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}});
    }
    const offlineManifest = /^\/offline\/(openwrt-(?:common|capture))\/manifest$/.exec(url.pathname);
    if (offlineManifest) {
      const object = await env.UPDATES.get("channel/" + offlineManifest[1] + ".json");
      const headers = {"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff", "Content-Type":"application/json; charset=utf-8"};
      if (!object) return new Response("Not found", {status:404, headers});
      if (object.size > 65536) return new Response("Invalid offline manifest", {status:503, headers});
      const body = await object.text();
      return new Response(request.method === "HEAD" ? null : body, {headers});
    }
    if (url.pathname === "/manifest") {
      const platforms=url.searchParams.getAll("platform");
      if(platforms.length>1 || [...url.searchParams.keys()].some(key=>key!=="platform"))
        return new Response("Invalid platform selector",{status:400});
      const platform=platforms[0] || "win-x64";
      if(!["win-x64","linux-x64"].includes(platform))
        return new Response("Unsupported platform",{status:400});
      const alphaKey=platform==="win-x64" ? "channel/manifest.json" : "channel/linux-x64/alpha.json";
      const stableKey=platform==="win-x64" ? "channel/stable.json" : "channel/linux-x64/stable.json";
      const code=request.headers.get("X-RouterManager-Test-Code") || "";
      if (code) {
        if (!/^[A-Za-z0-9_-]{6,96}$/.test(code)) return new Response("Invalid testing code",{status:403,headers:{"Cache-Control":"no-store"}});
        const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(code));
        const hex=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,"0")).join("");
        if (code.length>96 || !env.TEST_CODE_SHA256 || hex!==env.TEST_CODE_SHA256)
          return new Response("Invalid testing code",{status:403,headers:{"Cache-Control":"no-store"}});
      }
      let object=await env.UPDATES.get(code ? alphaKey : stableKey);
      if (!object && !code) object=await env.UPDATES.get(alphaKey);
      const headers={"Cache-Control":"no-store","Vary":"X-RouterManager-Test-Code","X-Content-Type-Options":"nosniff"};
      if (!object) return new Response(null,{status:204,headers});
      if (object.size>65536) return new Response("Invalid manifest",{status:503,headers});
      const text=await object.text();
      let version;
      try {
        const envelope=JSON.parse(text);
        if(envelope.state==="publishing") {
          if(envelope.product!=="RouterManager" || envelope.platform!==platform || envelope.channel!=="alpha")
            throw new Error("Invalid maintenance slot");
          if(!code)return new Response(null,{status:204,headers});
          headers["Retry-After"]="30";
          headers["X-RouterManager-Release-State"]="publishing";
          return new Response(request.method==="HEAD" ? null : "Alpha publication in progress",{status:503,headers});
        }
        const target=envelope.payload.platform || "win-x64";
        if(target!==platform)throw new Error("Cross-platform manifest");
        version=envelope.payload.version;
      }catch {return new Response("Invalid manifest",{status:503,headers});}
      if (!code && !/^\d+(?:\.\d+){1,3}$/.test(version)) return new Response(null,{status:204,headers});
      headers["Content-Type"]="application/json; charset=utf-8";
      return new Response(request.method==="HEAD" ? null : text,{status:200,headers});
    }
    let key;
    if (/^\/offline\/openwrt-common\/files\/[a-f0-9]{64}\/RouterManager_OpenWrt_Common_Offline_[0-9A-Za-z_-]+\.zip$/.test(url.pathname))
      key = url.pathname.slice(1);
    else if (/^\/offline\/openwrt-capture\/files\/[a-f0-9]{64}\/RouterManager_OpenWrt_Capture_Offline_[0-9A-Za-z_-]+\.zip$/.test(url.pathname))
      key = url.pathname.slice(1);
    else if (/^\/offline\/openwrt-(?:common|capture)\/manifests\/[a-f0-9]{64}\.json$/.test(url.pathname))
      key = url.pathname.slice(1);
    else if (/^\/files\/(?:(?:win-x64|linux-x64)\/)?[a-f0-9]{64}\/(?:RouterManager|LuXiaoGuan)_v[0-9A-Za-z._-]+\.zip$/.test(url.pathname))
      key = url.pathname.slice(1);
    else if (/^\/(blobs\/[a-f0-9]{64}\.z|catalog\/[a-f0-9]{64}\.json)$/.test(url.pathname))
      key = url.pathname.slice(1);
    else return new Response("Not found", {status: 404});
    const isFile = key.startsWith("files/") || /^offline\/openwrt-(?:common|capture)\/files\//.test(key);
    const resumable = isFile || key.startsWith("blobs/");
    let ranged = false;
    let options = {};
    const range = resumable && request.method === "GET" && request.headers.get("Range");
    const match = range && /^bytes=(\d*)-(\d*)$/.exec(range);
    if (match && (match[1] || match[2])) {
      const info = await env.UPDATES.head(key);
      if (!info) return new Response("Not found", {status: 404});
      const ifRange = request.headers.get("If-Range");
      if (!ifRange || ifRange === info.httpEtag) {
        const first = match[1] ? Number(match[1]) : Math.max(0, info.size - Number(match[2]));
        const last = match[1] && match[2] ? Math.min(Number(match[2]), info.size - 1) : info.size - 1;
        if (!Number.isSafeInteger(first) || !Number.isSafeInteger(last) ||
            first > last || first >= info.size || (!match[1] && Number(match[2]) === 0))
          return new Response(null, {status: 416, headers: {"Content-Range": "bytes */" + info.size, "Accept-Ranges": "bytes"}});
        options = {range: {offset: first, length: last - first + 1}};
        ranged = true;
      }
    }
    const object = request.method === "HEAD"
      ? await env.UPDATES.head(key) : await env.UPDATES.get(key, options);
    if (!object) return new Response("Not found", {status: 404});
    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("ETag", object.httpEtag);
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Cache-Control", key !== "channel/manifest.json"
      ? "public, max-age=31536000, immutable" : "public, max-age=300");
    headers.set("Content-Type", isFile ? "application/zip" : key.startsWith("blobs/") ? "application/octet-stream" : "application/json; charset=utf-8");
    headers.set("Content-Length", String(ranged && object.range ? object.range.length : object.size));
    if (resumable) headers.set("Accept-Ranges", "bytes");
    if (isFile) {
      headers.set("Content-Disposition", 'attachment; filename="' + key.split("/").pop() + '"');
    }
    let status = 200;
    if (ranged && object.range) {
      const {offset, length} = object.range;
      headers.set("Content-Range", "bytes " + offset + "-" + (offset + length - 1) + "/" + object.size);
      status = 206;
    }
    return new Response(request.method === "HEAD" ? null : object.body, {status, headers});
  }
};
