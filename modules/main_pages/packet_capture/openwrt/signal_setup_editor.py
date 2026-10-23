"""将真实设备选项装入编辑器，并生成执行器接受的白名单请求。"""
from modules.capture_services.openwrt.wireless_security import encryption_base
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest, validate_request
from .signal_setup_forms import clear_editor_update, bind_editor_update


def fill(box, options, current=None, *, preserve=False):
    """只保留设备提供的值；未知值不伪造支持。"""
    box.clear()
    values = list(options)
    if preserve:
        values.insert(0, ("沿用当前" + ("（" + str(current) + "）" if current not in (None, "") else "系统设置"), None))
    for label, value in values:
        if box.findData(value) < 0:
            box.addItem(label, value)
    if not preserve and current is not None:
        index = box.findData(current)
        if index < 0:
            box.addItem(str(current) + "（当前配置）", current)
            index = box.count() - 1
        box.setCurrentIndex(index)
    box.setEnabled(box.count() > (1 if preserve else 0))
    if box.count() == 0:
        box.addItem("设备未提供此项", None)
        box.setEnabled(False)


def configure_ap(dialog, environment):
    """高级射频设置默认保持原状，只提交用户明确选择的项。"""
    clear_editor_update(dialog, "encryption")
    data = environment.data
    radio = next(r for r in data["radios"] if r["name"] == dialog.radio_name)
    old = next((r for r in data["rows"] if dialog.row and r["section"] == dialog.row.section), {})
    dialog._old = old
    dialog.password.set_stored_available(bool(old.get("has_key")))
    fill(dialog.channel, ([("自动", "auto")] if data.get("features", {}).get("ap_acs") else []) + [(str(c["channel"]) + (" · DFS" if c.get("restricted") else ""), str(c["channel"]))
         for c in radio.get("channels", []) if not c.get("restricted")], radio.get("channel"), preserve=True)
    from .signal_radio_fields import configure_radio_fields
    configure_radio_fields(dialog, radio, data.get("features", {}))
    fill(dialog.country, [(c.get("iso3166", c.get("code", "")) + " · " + c.get("country", ""), c.get("iso3166", c.get("code")))
         for c in radio.get("countries", []) if len(c.get("iso3166", c.get("code", ""))) == 2], radio.get("country"), preserve=True)
    fill(dialog.power, [(str(p["dbm"]) + " dBm · " + str(p.get("mw", "?")) + " mW", str(p["dbm"]))
         for p in radio.get("powers", [])], radio.get("txpower"), preserve=True)
    supported = [(label, value) for label, value in dialog._encryption_choices
                 if value not in ("sae", "sae-mixed") or data.get("features", {}).get("ap_sae")]
    supported = [(label, value) for label, value in supported if value != "owe" or data.get("features", {}).get("ap_owe")]
    if radio.get("band") == "6g":
        supported = [(label, value) for label, value in supported if value in ("sae", "owe")]
    fill(dialog.encryption, supported, old.get("encryption", "sae" if radio.get("band") == "6g" else "psk2"))
    dialog.hidden.configure(old.get("hidden", "0"), preserve=False)
    dialog.isolation.configure(old.get("isolate", "0") if dialog.row else "1", preserve=False)
    dialog.client_limit.setText(str(old.get("maxassoc", "")))
    dialog.wmm.configure(old.get("wmm"))
    for control, key in ((dialog.management, "ieee80211w"),):
        control.setEnabled(True)
        control.setCurrentIndex(max(0, control.findData(old.get(key))))
    for control, key in ((dialog.dtim, "dtim_period"), (dialog.beacon, "beacon_int")):
        value = str((radio if key == "beacon_int" else old).get(key, ""))
        control.setText(value if value.isdigit() and int(value) > 0 else "")
    network = old.get("network", "lan")
    if isinstance(network, list):
        network = network[0] if len(network) == 1 else None
    fill(dialog.network, [(n["name"], n["name"]) for n in data["networks"] if n.get("name") != "loopback"], old.get("network", "lan"), preserve=bool(dialog.row))
    def encryption_changed():
        dialog.password.setEnabled(str(dialog.encryption.currentData()).split("+")[0] not in ("none", "owe"))
    bind_editor_update(dialog, "encryption", encryption_changed, dialog.encryption.currentIndexChanged)
    encryption_changed()
    if dialog.summary is not None:
        dialog.summary.setText(dialog._summary_base + " \u00b7 \u5f53\u524d\u4fe1\u9053 " + str(radio.get("channel", "\u81ea\u52a8")))
    dialog.expert_fields.configure(radio, old, data.get("features", {}))
    for key, control in (("hidden", dialog.hidden), ("isolate", dialog.isolation), ("maxassoc", dialog.client_limit),
            ("wmm", dialog.wmm), ("ieee80211w", dialog.management), ("dtim_period", dialog.dtim), ("beacon_int", dialog.beacon),
            ("channel", dialog.channel), ("country", dialog.country), ("txpower", dialog.power), ("encryption", dialog.encryption),
            ("htmode", dialog.standard_combo), ("htmode", dialog.bandwidth_combo)):
        supported = option_supported(radio, key)
        if not supported:
            control.setEnabled(False)
        elif not hasattr(control, "currentData"):
            control.setEnabled(True)
        if not supported:
            control.setToolTip("当前无线脚本未提供此参数，保留路由器原配置。")
    dialog.save_button.setEnabled(True)
    if dialog.save_only_button is not None:
        dialog.save_only_button.setEnabled(True)


def configure_relay(dialog, environment):
    """网络接口和安全区域读取原配置；新连接使用 DHCP 及现有 wan。"""
    clear_editor_update(dialog, "bridge")
    data = environment.data
    old = next((r for r in data["rows"] if dialog.row and r["section"] == dialog.row.section), {})
    dialog._old = old
    radio = next((r for r in data["radios"] if r.get("name") == (old.get("device") or dialog.radio_name)), {})
    dialog.lock_bssid.configure("1" if not dialog.row or old.get("bssid") else "0", preserve=False)
    dialog.lock_bssid.setEnabled(option_supported(radio, "bssid"))
    if not dialog.lock_bssid.isEnabled():
        dialog.lock_bssid.setToolTip("当前无线脚本未确认支持 BSSID 锁定，保留原配置。")
    wds_item = dialog.bridge_mode.model().item(dialog.bridge_mode.findData("wds"))
    wds_item.setEnabled(radio.get("type") == "mac80211" and option_supported(radio, "wds"))
    dialog.password.set_stored_available(bool(old.get("has_key")))
    network = old.get("network", "")
    if isinstance(network, list):
        network = network[0] if len(network) == 1 else ""
    info = next((n for n in data["networks"] if n["name"] == network), {})
    dialog.name_edit.setText(old.get("description") or dialog.name_edit.text())
    index = dialog.protocol.findData(info.get("proto", "dhcp"))
    dialog.protocol.setCurrentIndex(index)
    for field, key in ((dialog.address, "ipaddr"), (dialog.netmask, "netmask"), (dialog.gateway, "gateway")):
        value = info.get(key, "")
        field.setText(" ".join(value) if isinstance(value, list) else value)
    dns = info.get("dns", [])
    dialog.dns.set_values(dns if isinstance(dns, list) else str(dns).split())
    zones = data["zones"]
    current = next((z["name"] for z in zones if network and network in (z.get("network") if isinstance(z.get("network"), list) else str(z.get("network", "")).split())), "wan")
    fill(dialog.zone, [(z["name"] + (" · 已启用地址转换" if z.get("masq") == "1" else ""), z["name"]) for z in zones], current)
    dialog.metric_edit.setText(str(info.get("metric", "")))
    bridge = next((n for n in data["networks"] if n["name"] == old.get("rm_bridge")), {})
    mode = "relayd" if bridge else "wds" if old.get("wds") == "1" else "routed"
    downstream = (bridge.get("network") or ["lan"])[0] if bridge else network if mode == "wds" else "lan"
    fill(dialog.bridge_network, [(n["name"], n["name"]) for n in data["networks"] if n.get("name") != "loopback" and n.get("proto") != "relay"], downstream)
    dialog.relay_ip.setText(bridge.get("ipaddr", ""))
    dialog.bridge_mode.setCurrentIndex(max(0, dialog.bridge_mode.findData(mode)))
    def bridge_changed():
        if dialog.bridge_mode.currentData() == "relayd":
            selected = dialog.bridge_network.currentData()
            zone = next((z["name"] for z in zones if selected in (z.get("network") if isinstance(z.get("network"), list) else str(z.get("network", "")).split())), None)
            if zone is not None: dialog.zone.setCurrentIndex(dialog.zone.findData(zone))
    bind_editor_update(dialog, "bridge", bridge_changed,
                       dialog.bridge_mode.currentIndexChanged, dialog.bridge_network.currentIndexChanged)
    bridge_changed()
    dialog._network_mode_changed()
    dialog.identity_fields.configure(old, info)
    dialog.save_button.setEnabled(True)
    if dialog.save_only_button is not None:
        dialog.save_only_button.setEnabled(True)


def ap_request(dialog):
    """只生成当前界面中可执行的配置，不读取旧密码。"""
    opts = {}
    for key, control in (("channel", dialog.channel), ("htmode", dialog.bandwidth_combo),
        ("country", dialog.country), ("txpower", dialog.power), ("encryption", dialog.encryption),
        ("network", dialog.network), ("ieee80211w", dialog.management)):
        if control.isEnabled() and control.currentData() is not None:
            opts[key] = control.currentData()
    for key, control in (("hidden", dialog.hidden), ("isolate", dialog.isolation), ("wmm", dialog.wmm)):
        value = control.pending_value()
        if control.isEnabled() and value is not None:
            opts[key] = value
    for key, control in (("dtim_period", dialog.dtim), ("beacon_int", dialog.beacon), ("maxassoc", dialog.client_limit)):
        if control.isEnabled() and control.text().strip():
            opts[key] = control.text().strip()
    opts.update(dialog.expert_fields.values())
    return validate_request(WirelessRequest("ap", dialog.radio_name, dialog.name_edit.text(),
        dialog.password.submitted_text() if encryption_base(opts.get("encryption")) not in ("none", "owe") else "",
        section=dialog.row.section if dialog.row else "", options=opts))


def relay_request(dialog, network, token=""):
    """备注与真实上级名称分开，选择手动地址时必须填写掩码。"""
    mode = dialog.bridge_mode.currentData()
    opts = {"bridge_mode": mode, "encryption": network.encryption, "proto": dialog.protocol.currentData(),
            "zone": dialog.zone.currentData(), "dns": " ".join(dialog.dns.values())}
    if mode != "routed":
        opts["network"] = dialog.bridge_network.currentData()
    if mode == "relayd":
        opts["relay_ip"] = dialog.relay_ip.text().strip()
    if network.channel:
        opts["channel"] = str(network.channel)
    if dialog.lock_bssid.isEnabled():
        if dialog.lock_bssid.value() == "1":
            if not network.bssid:
                raise ValueError("没有可用的上级接入点地址，请先重新扫描加入。")
            opts["bssid"] = network.bssid
        else:
            opts["bssid"] = ""
    if mode == "wds":
        for key in ("proto", "zone", "dns"): opts.pop(key, None)
    if mode != "wds" and opts["proto"] == "static":
        opts.update(ipaddr=dialog.address.text().strip(), netmask=dialog.netmask.text().strip(), gateway=dialog.gateway.text().strip())
    if mode != "wds" and dialog.metric_edit.text().strip():
        opts["metric"] = dialog.metric_edit.text().strip()
    opts.update(dialog.identity_fields.values())
    return validate_request(WirelessRequest("sta", network.radio, network.ssid, dialog.password.submitted_text(),
        section=dialog.row.section if dialog.row else "", label=dialog.name_edit.text(),
        options=opts, verification=token))
