"""将真实设备选项装入编辑器，并生成执行器接受的白名单请求。"""
from modules.capture_services.openwrt.wireless_security import encryption_base
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from modules.capture_services.openwrt.uci_booleans import netifd_bool
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
    dialog.password.set_stored_available(bool(old.get("has_key")) and str(old.get("encryption", "psk2")).split("+", 1)[0] not in ("none", "owe"))
    fill(dialog.channel, ([("自动", "auto")] if data.get("features", {}).get("ap_acs") else []) + [(str(c["channel"]) + (" · DFS" if c.get("restricted") else ""), str(c["channel"]))
         for c in radio.get("channels", []) if not c.get("restricted")], radio.get("channel"), preserve=True)
    from .signal_radio_fields import configure_radio_fields, power_current_label
    configure_radio_fields(dialog, radio, data.get("features", {}))
    fill(dialog.country, [(c.get("iso3166", c.get("code", "")) + " · " + c.get("country", ""), c.get("iso3166", c.get("code")))
         for c in radio.get("countries", []) if len(c.get("iso3166", c.get("code", ""))) == 2], radio.get("country"), preserve=True)
    fill(dialog.power, [(str(p["dbm"]) + " dBm · " + str(p.get("mw", "?")) + " mW", str(p["dbm"]))
         for p in radio.get("powers", [])], radio.get("txpower"), preserve=True)
    dialog.power.setItemText(0, power_current_label(radio))
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
    dialog.password.setEnabled(True)
    if dialog.summary is not None:
        dialog.summary.setText(dialog._summary_base + " \u00b7 \u5f53\u524d\u4fe1\u9053 " + str(radio.get("channel", "\u81ea\u52a8")))
    dialog.expert_fields.configure(radio, old, data.get("features", {}))
    dialog.ap_identity.configure(old, option_supported(radio, "macaddr"), data.get("rows", ()))
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
    can_save = dialog.editing or dialog.ap_identity.supported
    if not can_save:
        dialog.message("\u5f53\u524d\u65e0\u7ebf\u811a\u672c\u672a\u786e\u8ba4\u652f\u6301\u6307\u5b9a BSSID\uff0c\u8bf7\u91cd\u65b0\u68c0\u6d4b\u3002")
    dialog.save_button.setEnabled(can_save)
    if dialog.save_only_button is not None:
        dialog.save_only_button.setEnabled(can_save)
    from .signal_setup_presets import bind_settings_profile
    bind_settings_profile(dialog, environment, "ap")


def configure_relay(dialog, environment):
    """网络接口和安全区域读取原配置；新连接使用 DHCP 及现有 wan。"""
    clear_editor_update(dialog, "bridge")
    data = environment.data
    old = next((r for r in data["rows"] if dialog.row and r["section"] == dialog.row.section), {})
    dialog._old = old
    radio = next((r for r in data["radios"] if r.get("name") == (old.get("device") or dialog.radio_name)), {})
    dialog._current_radio_channel = radio.get("channel")
    dialog.lock_bssid.configure("1" if not dialog.row or old.get("bssid") else "0", preserve=False)
    dialog.lock_bssid.setEnabled(option_supported(radio, "bssid"))
    if not dialog.lock_bssid.isEnabled():
        dialog.lock_bssid.setToolTip("当前无线脚本未确认支持 BSSID 锁定，保留原配置。")
    wds_item = dialog.bridge_mode.model().item(dialog.bridge_mode.findData("wds"))
    wds_item.setEnabled(radio.get("type") == "mac80211" and option_supported(radio, "wds"))
    dialog.password.set_stored_available(bool(old.get("has_key")))
    network = old.get("network", "")
    names = network if isinstance(network, list) else str(network or "").split()
    dialog.current_network.setText("、".join(names) if names else ("尚未读取" if dialog.row else "保存时自动创建"))
    network_label = "、".join(names) if names else ("尚未读取" if dialog.row else "保存时自动创建")
    choices = [(network_label, None)]
    if dialog.row:
        choices += [(n["name"], n["name"]) for n in data["networks"] if n.get("name") not in ("lan", "loopback") and n.get("proto") != "relay" and n.get("name") not in names]
    fill(dialog.uplink_network, choices, None)
    dialog.current_bssid.setText(old.get("bssid", "") or getattr(dialog, "_target_bssid", ""))
    dialog.current_bssid.setEnabled(dialog.lock_bssid.isEnabled())
    choices = [("Open", "none"), ("WPA-PSK", "psk"), ("WPA2-PSK", "psk2"), ("WPA/WPA2-PSK", "psk-mixed")]
    if data.get("features", {}).get("sta_sae"):
        choices.extend((("WPA3-SAE", "sae"), ("WPA2/WPA3", "sae-mixed")))
    if data.get("features", {}).get("sta_owe"):
        choices.append(("OWE", "owe"))
    fill(dialog.current_encryption, choices, old.get("encryption") or getattr(dialog, "_target_encryption", "psk2"))
    dialog.current_encryption.setEnabled(option_supported(radio, "encryption"))
    dialog.interface_name.setText(old.get("ifname", ""))
    dialog.interface_name.setEnabled(option_supported(radio, "ifname"))
    management = old.get("ieee80211w")
    dialog.management.setItemText(0, "沿用当前" + (" (" + str(management) + ")" if management is not None else ""))
    dialog.management.setCurrentIndex(0)
    dialog.management.setEnabled(option_supported(radio, "ieee80211w"))
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
    try:
        existing_wds = netifd_bool(old.get("wds"), default=False)
    except ValueError:
        existing_wds = None
    if existing_wds is None:
        dialog.bridge_mode.addItem("\u65e0\u6cd5\u786e\u8ba4\u539f WDS \u8bbe\u7f6e", "unknown")
    mode = "unknown" if existing_wds is None else "relayd" if bridge else "wds" if existing_wds else "routed"
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
    dialog.save_button.setEnabled(existing_wds is not None)
    if dialog.save_only_button is not None:
        dialog.save_only_button.setEnabled(existing_wds is not None)
    if existing_wds is None:
        dialog.message("\u65e0\u6cd5\u786e\u8ba4\u8def\u7531\u5668\u539f WDS \u5f00\u5173\u7684\u542b\u4e49\uff0c\u8bf7\u5148\u5728\u8def\u7531\u5668\u4e2d\u6838\u5bf9\u3002", error=True)
    from .signal_setup_presets import bind_settings_profile
    bind_settings_profile(dialog, environment, "relay")
    dialog._network_baseline = relay_network_values(dialog)


def relay_network_values(dialog):
    return (dialog.bridge_mode.currentData(), dialog.bridge_network.currentData(),
            dialog.relay_ip.text(), dialog.protocol.currentData(), dialog.address.text(),
            dialog.netmask.text(), dialog.gateway.text(), tuple(dialog.dns.values()),
            dialog.zone.currentData(), dialog.metric_edit.text())


def ap_request(dialog):
    """只生成当前界面中可执行的配置，不读取旧密码。"""
    opts = {}
    for key, control in (("channel", dialog.channel), ("htmode", dialog.bandwidth_combo),
        ("country", dialog.country), ("txpower", dialog.power), ("encryption", dialog.encryption),
        ("network", dialog.network), ("ieee80211w", dialog.management)):
        if control.isEnabled() and control.currentData() is not None:
            opts[key] = control.currentData()
    if dialog.settings_profile.currentData() == "recommended" and opts.get("channel") == "auto":
        opts["recommended_auto"] = True
    for key, control in (("hidden", dialog.hidden), ("isolate", dialog.isolation), ("wmm", dialog.wmm)):
        value = control.pending_value()
        if control.isEnabled() and value is not None:
            opts[key] = value
    for key, control in (("dtim_period", dialog.dtim), ("beacon_int", dialog.beacon), ("maxassoc", dialog.client_limit)):
        if control.isEnabled() and control.text().strip():
            opts[key] = control.text().strip()
    opts.update(dialog.expert_fields.values())
    address = dialog.ap_identity.value()
    if address is not None:
        opts["macaddr"] = address
    return validate_request(WirelessRequest("ap", dialog.radio_name, dialog.name_edit.text(),
        dialog.password.submitted_text() if encryption_base(opts.get("encryption")) not in ("none", "owe") else "",
        section=dialog.row.section if dialog.row else "", options=opts))


def relay_request(dialog, network, token=""):
    """备注与真实上级名称分开，选择手动地址时必须填写掩码。"""
    mode = dialog.bridge_mode.currentData()
    opts = {"bridge_mode": mode, "encryption": dialog.current_encryption.currentData() if dialog.current_encryption.isEnabled() else network.encryption, "proto": dialog.protocol.currentData(),
            "zone": dialog.zone.currentData(), "dns": " ".join(dialog.dns.values())}
    if mode != "routed":
        opts["network"] = dialog.bridge_network.currentData()
    if mode == "relayd":
        opts["relay_ip"] = dialog.relay_ip.text().strip()
    current_ssid = dialog.target_label.text() if dialog.row else network.ssid
    current_bssid = dialog.current_bssid.text().strip() or network.bssid
    target_changed = bool(dialog.row and (current_ssid != network.ssid or
                          (dialog.lock_bssid.isEnabled() and dialog.lock_bssid.value() == "1" and current_bssid != network.bssid)))
    if target_changed and current_ssid != network.ssid and network.bssid and current_bssid == network.bssid and dialog.lock_bssid.value() == "1":
        raise ValueError("\u4fe1\u53f7\u540d\u79f0\u5df2\u66f4\u6539\uff0c\u8bf7\u66f4\u65b0\u4e0a\u7ea7\u65e0\u7ebf\u5730\u5740\u6216\u53d6\u6d88\u9501\u5b9a\u3002")
    if target_changed and str(getattr(dialog, "_current_radio_channel", "") or "").lower() not in ("auto", "0"):
        raise ValueError("\u5f53\u524d\u7f51\u5361\u7684\u4fe1\u9053\u5df2\u56fa\u5b9a\u6216\u65e0\u6cd5\u786e\u8ba4\uff0c\u65e0\u6cd5\u786e\u8ba4\u65b0\u4e0a\u7ea7\u7684\u4fe1\u9053\u3002\u8bf7\u5148\u626b\u63cf\u5e76\u91cd\u65b0\u9009\u62e9\u8be5\u4fe1\u53f7\u3002")
    if network.channel and not target_changed:
        opts["channel"] = str(network.channel)
    if dialog.lock_bssid.isEnabled():
        if dialog.lock_bssid.value() == "1":
            selected_bssid = dialog.current_bssid.text().strip() or network.bssid
            if not selected_bssid:
                raise ValueError("没有可用的上级接入点地址，请先重新扫描加入。")
            opts["bssid"] = selected_bssid
        else:
            opts["bssid"] = ""
    if mode == "wds":
        for key in ("proto", "zone", "dns"): opts.pop(key, None)
    if mode != "wds" and opts["proto"] == "static":
        opts.update(ipaddr=dialog.address.text().strip(), netmask=dialog.netmask.text().strip(), gateway=dialog.gateway.text().strip())
    if mode != "wds" and dialog.metric_edit.text().strip():
        opts["metric"] = dialog.metric_edit.text().strip()
    opts.update(dialog.identity_fields.values())
    if dialog.interface_name.isEnabled() and dialog.interface_name.text().strip() != dialog._old.get("ifname", ""):
        opts["ifname"] = dialog.interface_name.text().strip()
    if dialog.management.isEnabled() and dialog.management.currentData() is not None:
        opts["ieee80211w"] = dialog.management.currentData()
    if dialog.row and relay_network_values(dialog) == getattr(dialog, "_network_baseline", None):
        opts["preserve_network"] = True
        old_network = dialog._old.get("network", "")
        opts["preserve_networks"] = old_network if isinstance(old_network, list) else str(old_network or "").split()
        if mode == "routed" and dialog.uplink_network.currentData():
            opts["bind_network"] = dialog.uplink_network.currentData()
        for key in ("proto", "zone", "dns", "ipaddr", "netmask", "gateway", "metric", "relay_ip"):
            opts.pop(key, None)
    if dialog.row and mode == "routed" and dialog.uplink_network.currentData() and not opts.get("preserve_network"):
        raise ValueError("更换关联网络时，请先保持其他网络设置不变并保存；再重新打开修改地址。")
    ssid = dialog.target_label.text() if dialog.row else network.ssid
    return validate_request(WirelessRequest("sta", network.radio, ssid, dialog.password.submitted_text(),
        section=dialog.row.section if dialog.row else "", label=dialog.name_edit.text(),
        options=opts, verification=token))
